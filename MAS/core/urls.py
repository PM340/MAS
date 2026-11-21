from django.urls import path
from . import views

urlpatterns = [
    # Auth & Profile
    path('', views.home_redirect, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('force-password-change/', views.force_password_change_view, name='force_password_change'),
    path('profile/', views.profile_view, name='profile'),
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # Super Admin: Bulk Excel Onboarding, Printable Roster, Users & Classes
    path('onboarding/', views.bulk_onboarding_view, name='bulk_onboarding'),
    path('onboarding/sample/', views.download_sample_excel, name='download_sample_excel'),
    path('onboarding/credentials/', views.download_credentials_excel, name='download_credentials_excel'),
    path('onboarding/roster/', views.printable_roster_view, name='printable_roster'),
    path('users/', views.manage_users_view, name='manage_users'),
    path('users/<int:user_id>/edit/', views.edit_user_view, name='edit_user'),
    path('users/<int:user_id>/reset-password/', views.reset_user_password_view, name='reset_user_password'),
    path('users/<int:user_id>/delete/', views.delete_user_view, name='delete_user'),
    path('classes/', views.manage_classes_view, name='manage_classes'),
    path('classes/<int:class_id>/delete/', views.delete_class_view, name='delete_class'),
    path('subjects/<int:subject_id>/delete/', views.delete_subject_view, name='delete_subject'),

    # Question Bank, Printable Paper & Moodle Exam Engine
    path('questions/', views.question_bank_view, name='question_bank'),
    path('questions/create/', views.question_create_view, name='question_create'),
    path('questions/import-pdf/', views.question_import_pdf_view, name='question_import_pdf'),
    path('questions/<int:question_id>/edit/', views.question_edit_view, name='question_edit'),
    path('questions/<int:question_id>/delete/', views.question_delete_view, name='question_delete'),
    path('exams/', views.exam_list_view, name='exam_list'),
    path('exams/create/', views.exam_create_view, name='exam_create'),
    path('exams/format-generator/', views.format_paper_generator_view, name='format_paper_generator'),
    path('exams/<int:exam_id>/format-generator/', views.format_paper_generator_view, name='format_paper_generator_for_exam'),
    path('exams/format-generator/sample-docx/', views.download_sample_format_docx, name='download_sample_format_docx'),
    path('exams/<int:exam_id>/download/<str:output_format>/', views.download_formatted_paper, name='download_formatted_paper'),
    path('exams/<int:exam_id>/edit/', views.exam_edit_view, name='exam_edit'),
    path('exams/<int:exam_id>/paper/', views.printable_paper_view, name='printable_paper'),
    path('exams/<int:exam_id>/take/', views.take_exam_view, name='take_exam'),
    path('attempts/<int:attempt_id>/save-answer/', views.save_exam_answer_api, name='save_exam_answer'),
    path('attempts/<int:attempt_id>/log-tab-switch/', views.log_tab_switch_api, name='log_tab_switch'),
    path('attempts/<int:attempt_id>/heartbeat/', views.exam_heartbeat_api, name='exam_heartbeat'),
    path('attempts/<int:attempt_id>/submit/', views.submit_exam_view, name='submit_exam'),
    path('attempts/<int:attempt_id>/result/', views.exam_result_view, name='exam_result'),
    path('exams/<int:exam_id>/attempts/', views.exam_attempts_view, name='exam_attempts'),
    path('attempts/<int:attempt_id>/grade/', views.grade_attempt_view, name='grade_attempt'),

    # Assignments
    path('assignments/', views.assignment_list_view, name='assignment_list'),
    path('assignments/create/', views.assignment_create_view, name='assignment_create'),
    path('assignments/<int:assignment_id>/', views.assignment_detail_view, name='assignment_detail'),
    path('submissions/<int:submission_id>/grade/', views.grade_submission_view, name='grade_submission'),

    # Attendance
    path('attendance/take/', views.take_attendance_view, name='take_attendance'),
    path('attendance/my/', views.student_attendance_view, name='student_attendance'),
    path('attendance/report/', views.admin_attendance_report_view, name='admin_attendance_report'),
    path('attendance/report/export/', views.export_attendance_excel, name='export_attendance_excel'),
    path('attendance/report/print/', views.printable_attendance_register, name='printable_attendance_register'),

    # Messaging & Notice Board
    path('messages/', views.inbox_view, name='inbox'),
    path('messages/compose/', views.compose_message_view, name='compose_message'),
    path('messages/<int:message_id>/', views.message_detail_view, name='message_detail'),
]
