"""Tests for certificate Django models. """


import json
from unittest.mock import patch

import pytest
import ddt
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from opaque_keys.edx.locator import CourseKey, CourseLocator
from path import Path as path

from common.djangoapps.student.tests.factories import AdminFactory, UserFactory
from lms.djangoapps.certificates.models import (
    CertificateGenerationHistory,
    CertificateHtmlViewConfiguration,
    CertificateInvalidation,
    CertificateStatuses,
    CertificateTemplateAsset,
    ExampleCertificate,
    ExampleCertificateSet,
    GeneratedCertificate
)
from lms.djangoapps.certificates.tests.factories import CertificateInvalidationFactory, GeneratedCertificateFactory
from lms.djangoapps.instructor_task.tests.factories import InstructorTaskFactory
from openedx.core.djangoapps.content.course_overviews.tests.factories import CourseOverviewFactory
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

FEATURES_INVALID_FILE_PATH = settings.FEATURES.copy()
FEATURES_INVALID_FILE_PATH['CERTS_HTML_VIEW_CONFIG_PATH'] = 'invalid/path/to/config.json'

TEST_DIR = path(__file__).dirname()
TEST_DATA_DIR = 'common/test/data/'
PLATFORM_ROOT = TEST_DIR.parent.parent.parent.parent
TEST_DATA_ROOT = PLATFORM_ROOT / TEST_DATA_DIR


class ExampleCertificateTest(TestCase):
    """Tests for the ExampleCertificate model. """

    COURSE_KEY = CourseLocator(org='test', course='test', run='test')

    DESCRIPTION = 'test'
    TEMPLATE = 'test.pdf'
    DOWNLOAD_URL = 'http://www.example.com'
    ERROR_REASON = 'Kaboom!'

    def setUp(self):
        super().setUp()
        self.cert_set = ExampleCertificateSet.objects.create(course_key=self.COURSE_KEY)
        self.cert = ExampleCertificate.objects.create(
            example_cert_set=self.cert_set,
            description=self.DESCRIPTION,
            template=self.TEMPLATE
        )

    def test_update_status_success(self):
        self.cert.update_status(
            ExampleCertificate.STATUS_SUCCESS,
            download_url=self.DOWNLOAD_URL
        )
        assert self.cert.status_dict ==\
               {'description': self.DESCRIPTION,
                'status': ExampleCertificate.STATUS_SUCCESS,
                'download_url': self.DOWNLOAD_URL}

    def test_update_status_error(self):
        self.cert.update_status(
            ExampleCertificate.STATUS_ERROR,
            error_reason=self.ERROR_REASON
        )
        assert self.cert.status_dict ==\
               {'description': self.DESCRIPTION,
                'status': ExampleCertificate.STATUS_ERROR,
                'error_reason': self.ERROR_REASON}

    def test_update_status_invalid(self):
        with self.assertRaisesRegex(ValueError, 'status'):
            self.cert.update_status('invalid')

    def test_latest_status_unavailable(self):
        # Delete any existing statuses
        ExampleCertificateSet.objects.all().delete()

        # Verify that the "latest" status is None
        result = ExampleCertificateSet.latest_status(self.COURSE_KEY)
        assert result is None

    def test_latest_status_is_course_specific(self):
        other_course = CourseLocator(org='other', course='other', run='other')
        result = ExampleCertificateSet.latest_status(other_course)
        assert result is None


class CertificateHtmlViewConfigurationTest(TestCase):
    """
    Test the CertificateHtmlViewConfiguration model.
    """
    def setUp(self):
        super().setUp()
        self.configuration_string = """{
            "default": {
                "url": "http://www.edx.org",
                "logo_src": "http://www.edx.org/static/images/logo.png"
            },
            "honor": {
                "logo_src": "http://www.edx.org/static/images/honor-logo.png"
            }
        }"""
        self.config = CertificateHtmlViewConfiguration(configuration=self.configuration_string)

    def test_create(self):
        """
        Tests creation of configuration.
        """
        self.config.save()
        assert self.config.configuration == self.configuration_string

    def test_clean_bad_json(self):
        """
        Tests if bad JSON string was given.
        """
        self.config = CertificateHtmlViewConfiguration(configuration='{"bad":"test"')
        pytest.raises(ValidationError, self.config.clean)

    def test_get(self):
        """
        Tests get configuration from saved string.
        """
        self.config.enabled = True
        self.config.save()
        expected_config = {
            "default": {
                "url": "http://www.edx.org",
                "logo_src": "http://www.edx.org/static/images/logo.png"
            },
            "honor": {
                "logo_src": "http://www.edx.org/static/images/honor-logo.png"
            }
        }
        assert self.config.get_config() == expected_config

    def test_get_not_enabled_returns_blank(self):
        """
        Tests get configuration that is not enabled.
        """
        self.config.enabled = False
        self.config.save()
        assert len(self.config.get_config()) == 0

    @override_settings(FEATURES=FEATURES_INVALID_FILE_PATH)
    def test_get_no_database_no_file(self):
        """
        Tests get configuration that is not enabled.
        """
        self.config.configuration = ''
        self.config.save()
        assert self.config.get_config() == {}


class CertificateTemplateAssetTest(TestCase):
    """
    Test Assets are uploading/saving successfully for CertificateTemplateAsset.
    """
    def test_asset_file_saving_with_actual_name(self):
        """
        Verify that asset file is saving with actual name, No hash tag should be appended with the asset filename.
        """
        CertificateTemplateAsset(description='test description', asset=SimpleUploadedFile(
            'picture1.jpg',
            b'these are the file contents!')).save()
        certificate_template_asset = CertificateTemplateAsset.objects.get(id=1)
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture1.jpg'

        # Now save asset with same file again, New file will be uploaded after deleting the old one with the same name.
        certificate_template_asset.asset = SimpleUploadedFile('picture1.jpg', b'file contents')
        certificate_template_asset.save()
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture1.jpg'

        # Now replace the asset with another file
        certificate_template_asset.asset = SimpleUploadedFile('picture2.jpg', b'file contents')
        certificate_template_asset.save()

        certificate_template_asset = CertificateTemplateAsset.objects.get(id=1)
        assert certificate_template_asset.asset == 'certificate_template_assets/1/picture2.jpg'


class EligibleCertificateManagerTest(SharedModuleStoreTestCase):
    """
    Test the GeneratedCertificate model's object manager for filtering
    out ineligible certs.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.course1 = CourseOverviewFactory()
        self.course2 = CourseOverviewFactory(
            id=CourseKey.from_string(f'{self.course1.id}a')
        )

        self.eligible_cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course1.id
        )
        self.ineligible_cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.audit_passing,
            user=self.user,
            course_id=self.course2.id
        )

    def test_filter_ineligible_certificates(self):
        """
        Verify that the EligibleAvailableCertificateManager filters out
        certificates marked as ineligible, and that the default object
        manager for GeneratedCertificate does not filter them out.
        """
        assert list(GeneratedCertificate.eligible_available_certificates.filter(user=self.user)) == [self.eligible_cert]
        assert list(GeneratedCertificate.objects.filter(user=self.user)) == [self.eligible_cert, self.ineligible_cert]

    def test_filter_certificates_for_nonexistent_courses(self):
        """
        Verify that the EligibleAvailableCertificateManager filters out
        certificates for courses with no CourseOverview.
        """
        self.course1.delete()
        assert not GeneratedCertificate.eligible_available_certificates.filter(user=self.user)


@ddt.ddt
class TestCertificateGenerationHistory(TestCase):
    """
    Test the CertificateGenerationHistory model's methods
    """
    @ddt.data(
        ({"student_set": "whitelisted_not_generated"}, "For exceptions", True),
        ({"student_set": "whitelisted_not_generated"}, "For exceptions", False),
        # check "students" key for backwards compatibility
        ({"students": [1, 2, 3]}, "For exceptions", True),
        ({"students": [1, 2, 3]}, "For exceptions", False),
        ({}, "All learners", True),
        ({}, "All learners", False),
        # test single status to regenerate returns correctly
        ({"statuses_to_regenerate": ['downloadable']}, 'already received', True),
        ({"statuses_to_regenerate": ['downloadable']}, 'already received', False),
        # test that list of > 1 statuses render correctly
        ({"statuses_to_regenerate": ['downloadable', 'error']}, 'already received, error states', True),
        ({"statuses_to_regenerate": ['downloadable', 'error']}, 'already received, error states', False),
        # test that only "readable" statuses are returned
        ({"statuses_to_regenerate": ['downloadable', 'not_readable']}, 'already received', True),
        ({"statuses_to_regenerate": ['downloadable', 'not_readable']}, 'already received', False),
    )
    @ddt.unpack
    def test_get_certificate_generation_candidates(self, task_input, expected, is_regeneration):
        staff = AdminFactory.create()
        instructor_task = InstructorTaskFactory.create(
            task_input=json.dumps(task_input),
            requester=staff,
            task_key='',
            task_id='',
        )
        certificate_generation_history = CertificateGenerationHistory(
            course_id=instructor_task.course_id,
            generated_by=staff,
            instructor_task=instructor_task,
            is_regeneration=is_regeneration,
        )
        assert certificate_generation_history.get_certificate_generation_candidates() == expected

    @ddt.data((True, "regenerated"), (False, "generated"))
    @ddt.unpack
    def test_get_task_name(self, is_regeneration, expected):
        staff = AdminFactory.create()
        instructor_task = InstructorTaskFactory.create(
            task_input=json.dumps({}),
            requester=staff,
            task_key='',
            task_id='',
        )
        certificate_generation_history = CertificateGenerationHistory(
            course_id=instructor_task.course_id,
            generated_by=staff,
            instructor_task=instructor_task,
            is_regeneration=is_regeneration,
        )
        assert certificate_generation_history.get_task_name() == expected


class CertificateInvalidationTest(SharedModuleStoreTestCase):
    """
    Test for the Certificate Invalidation model.
    """

    def setUp(self):
        super().setUp()
        self.course = CourseFactory()
        self.course_overview = CourseOverviewFactory.create(
            id=self.course.id
        )
        self.user = UserFactory()
        self.course_id = self.course.id  # pylint: disable=no-member
        self.certificate = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_id
        )

    def test_is_certificate_invalid_method(self):
        """ Verify that method return false if certificate is valid. """

        assert not CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

    def test_is_certificate_invalid_with_invalid_cert(self):
        """ Verify that method return true if certificate is invalid. """

        invalid_cert = CertificateInvalidationFactory.create(
            generated_certificate=self.certificate,
            invalidated_by=self.user
        )
        # Invalidate user certificate
        self.certificate.invalidate()
        assert CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

        # mark the entry as in-active.
        invalid_cert.active = False
        invalid_cert.save()

        # After making the certificate valid method will return false.
        assert not CertificateInvalidation.has_certificate_invalidation(self.user, self.course_id)

    @patch('openedx.core.djangoapps.programs.tasks.revoke_program_certificates.delay')
    @patch(
        'openedx.core.djangoapps.credentials.models.CredentialsApiConfig.is_learner_issuance_enabled',
        return_value=True,
    )
    def test_revoke_program_certificates(self, mock_issuance, mock_revoke_task):    # pylint: disable=unused-argument
        """ Verify that `revoke_program_certificates` is invoked upon invalidation. """
        # Invalidate user certificate
        self.certificate.invalidate()

        assert mock_revoke_task.call_count == 1
        assert mock_revoke_task.call_args[0] == (self.user.username, str(self.course_id))


class GeneratedCertificateTest(SharedModuleStoreTestCase):
    """
    Test GeneratedCertificates
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory()

        self.course = CourseOverviewFactory()
        self.course_key = self.course.id

    def _assert_event_data(self, mocked_function_call, expected_event_data):
        """Utility function that verifies the mocked function was called with the expected arguments."""

        mocked_function_call.assert_called_with(
            'revoked',
            self.user,
            str(self.course_key),
            event_data=expected_event_data
        )

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_invalidate(self, mock_emit_certificate_event):
        """
        Test the invalidate method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key
        )
        source = 'invalidated_test'
        cert.invalidate(source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        assert cert.status == CertificateStatuses.unavailable

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': cert.mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_notpassing(self, mock_emit_certificate_event):
        """
        Test the notpassing method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key
        )
        grade = '.3'
        source = "notpassing_test"
        cert.mark_notpassing(grade, source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        assert cert.status == CertificateStatuses.notpassing
        assert cert.grade == grade

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': cert.mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)

    @patch('lms.djangoapps.certificates.utils.emit_certificate_event')
    def test_unverified(self, mock_emit_certificate_event):
        """
        Test the unverified method
        """
        cert = GeneratedCertificateFactory.create(
            status=CertificateStatuses.downloadable,
            user=self.user,
            course_id=self.course_key
        )
        source = "unverified_test"
        cert.mark_unverified(source=source)

        cert = GeneratedCertificate.objects.get(user=self.user, course_id=self.course_key)
        assert cert.status == CertificateStatuses.unverified

        expected_event_data = {
            'user_id': self.user.id,
            'course_id': str(self.course_key),
            'certificate_id': cert.verify_uuid,
            'enrollment_mode': cert.mode,
            'source': source,
        }

        self._assert_event_data(mock_emit_certificate_event, expected_event_data)
