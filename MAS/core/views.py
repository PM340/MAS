import base64
import io
import json
import random
import re
import zipfile
from datetime import datetime

import openpyxl
#import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .context_processors import get_unread_message_count, get_visible_messages_for_user
from .decorators import role_required
from .forms import (
    AcademicClassForm,
    AdminUserForm,
    AssignmentForm,
    AssignmentGradeForm,
    AssignmentSubmissionForm,
    BulkOnboardingForm,
    ExamForm,
    ForcePasswordChangeForm,
    LoginForm,
    MessageComposeForm,
    PaperFormatUploadForm,
    PdfQuestionUploadForm,
    ProfileUpdateForm,
    QuestionForm,
    SubjectForm,
    UserCreateForm,
)
from .services import (
    build_sample_format_docx,
    extract_questions_from_pdf,
    generate_pdf_paper,
    generate_word_paper,
    parse_paper_format,
)
from .models import (
    AcademicClass,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    CustomUser,
    Exam,
    ExamAnswer,
    ExamAttempt,
    ExamQuestion,
    Message,
    MessageReadReceipt,
    Question,
    Subject,
    StudentClass
)


# ---------------------------------------------------------------------------
# Authentication, Forced First-Login Password Change & Profile
# ---------------------------------------------------------------------------

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')


def _ensure_session_key(request):
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key


def _terminate_attempt_for_second_device(attempt, reason):
    """Auto-submit and lock an active exam when another phone/device logs in or opens it."""
    if attempt.status == ExamAttempt.STATUS_IN_PROGRESS:
        attempt.tab_switch_count += 1
        log = list(attempt.tab_switch_log or [])
        log.append({
            'count': attempt.tab_switch_count,
            'reason': reason,
            'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
        attempt.tab_switch_log = log
        attempt.termination_reason = reason[:255]
        attempt.evaluate_attempt(auto_submitted=True)


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        roll_number = form.cleaned_data['roll_number'].strip().upper()
        password = form.cleaned_data['password']
        user = authenticate(request, username=roll_number, password=password)
        if user is not None:
            if not user.is_active:
                messages.error(request, 'This account is inactive. Please contact Super Admin.')
            else:
                login(request, user)
                new_session_key = _ensure_session_key(request)

                # Multi-device anti-cheating: if student has an active exam on another phone/device,
                # immediately auto-submit & terminate it and mark active_session_key to this device.
                terminated_any = False
                if user.is_student:
                    active_attempts = ExamAttempt.objects.filter(
                        student=user,
                        status=ExamAttempt.STATUS_IN_PROGRESS,
                    )
                    for att in active_attempts:
                        if att.active_session_key and att.active_session_key != new_session_key:
                            _terminate_attempt_for_second_device(
                                att,
                                'Exam auto-submitted & closed: Student logged in from another phone/device.',
                            )
                            terminated_any = True

                user.active_session_key = new_session_key
                user.save(update_fields=['active_session_key'])

                if terminated_any:
                    messages.warning(
                        request,
                        'Security Alert: An active exam running on another phone/device was automatically submitted and closed.',
                    )

                if user.must_change_password:
                    messages.warning(
                        request,
                        'Security Notice: This is your first login. You must set a private password to continue.',
                    )
                    return redirect('force_password_change')
                messages.success(request, f'Welcome back, {user.full_name}!')
                return redirect('dashboard')
        else:
            messages.error(request, 'Invalid Roll Number / System ID or password.')

    return render(request, 'auth/login.html', {'form': form})
def edit_subject(request, pk):
    subject = get_object_or_404(Subject, pk=pk)
    if request.method == 'POST':
        subject.title = request.POST.get('title')  # Adjust field names to match models.py
        subject.code = request.POST.get('code')
        subject.save()
        return redirect('dashboard')  # Replace with your redirect URL name
    return render(request, 'edit_subject.html', {'subject': subject})

def edit_class(request, pk):
    student_class = get_object_or_404(StudentClass, pk=pk)
    if request.method == 'POST':
        student_class.name = request.POST.get('name')  # Adjust field names to match models.py
        student_class.save()
        return redirect('dashboard')  # Replace with your redirect URL name
    return render(request, 'edit_class.html', {'student_class': student_class})

@login_required
def logout_view(request):
    logout(request)
    messages.info(request, 'You have been signed out safely.')
    return redirect('login')


@login_required
def force_password_change_view(request):
    if not request.user.must_change_password:
        return redirect('dashboard')

    form = ForcePasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        new_pwd = form.cleaned_data['new_password']
        request.user.set_password(new_pwd)
        request.user.must_change_password = False
        request.user.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Your private password has been set! Welcome to Acadex LMS.')
        return redirect('dashboard')

    return render(request, 'auth/force_password_change.html', {'form': form})


@login_required
def profile_view(request):
    form = ProfileUpdateForm(request.POST or None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        new_pwd = form.cleaned_data.get('new_password')
        if new_pwd:
            user.set_password(new_pwd)
            user.must_change_password = False
        user.save()
        if new_pwd:
            update_session_auth_hash(request, user)
        messages.success(request, 'Your profile has been updated.')
        return redirect('profile')

    return render(request, 'auth/profile.html', {'form': form})


# ---------------------------------------------------------------------------
# Role-Based Dashboards
# ---------------------------------------------------------------------------

@login_required
def dashboard_view(request):
    user = request.user
    recent_notices = get_visible_messages_for_user(user)[:5]
    read_ids = set(MessageReadReceipt.objects.filter(user=user).values_list('message_id', flat=True))

    if user.is_super_admin:
        context = {
            'total_students': CustomUser.objects.filter(role=CustomUser.ROLE_STUDENT).count(),
            'total_teachers': CustomUser.objects.filter(role=CustomUser.ROLE_TEACHER).count(),
            'total_classes': AcademicClass.objects.count(),
            'total_subjects': Subject.objects.count(),
            'total_questions': Question.objects.count(),
            'total_exams': Exam.objects.count(),
            'total_assignments': Assignment.objects.count(),
            'pending_password_users': CustomUser.objects.filter(must_change_password=True).count(),
            'recent_exams': Exam.objects.select_related('academic_class', 'subject')[:5],
            'recent_sessions': AttendanceSession.objects.select_related('academic_class', 'subject')[:5],
            'classes': AcademicClass.objects.annotate(
                student_count=Count('students', distinct=True),
                subject_count=Count('subjects', distinct=True),
            ),
            'recent_notices': recent_notices,
            'read_ids': read_ids,
        }
        return render(request, 'dashboard/admin_dashboard.html', context)

    if user.is_teacher:
        assigned_classes = user.assigned_classes.all()
        class_filter = Q(academic_class__in=assigned_classes) | Q(created_by=user)
        if not assigned_classes.exists():
            class_filter = Q()  # Show all if no specific class restriction assigned

        exams = Exam.objects.filter(class_filter).select_related('academic_class', 'subject').distinct()[:6]
        assignments = Assignment.objects.filter(class_filter).select_related('academic_class', 'subject').distinct()[:6]
        ungraded_submissions = AssignmentSubmission.objects.filter(
            marks_obtained__isnull=True
        ).select_related('assignment', 'student')[:6]
        ungraded_attempts = ExamAttempt.objects.filter(
            status__in=[ExamAttempt.STATUS_SUBMITTED, ExamAttempt.STATUS_AUTO_SUBMITTED],
            is_fully_graded=False,
        ).select_related('exam', 'student')[:6]

        context = {
            'assigned_classes': assigned_classes,
            'total_questions': Question.objects.count(),
            'total_exams': Exam.objects.count(),
            'total_assignments': Assignment.objects.count(),
            'sessions_taken': AttendanceSession.objects.filter(taken_by=user).count(),
            'exams': exams,
            'assignments': assignments,
            'ungraded_submissions': ungraded_submissions,
            'ungraded_attempts': ungraded_attempts,
            'recent_notices': recent_notices,
            'read_ids': read_ids,
        }
        return render(request, 'dashboard/teacher_dashboard.html', context)

    # Student Dashboard
    student_class = user.student_class
    exams = (
        Exam.objects.filter(academic_class=student_class, is_published=True).select_related('subject')
        if student_class else Exam.objects.none()
    )
    attempts_map = {
        att.exam_id: att
        for att in ExamAttempt.objects.filter(student=user).select_related('exam')
    }

    assignments = (
        Assignment.objects.filter(academic_class=student_class).select_related('subject')
        if student_class else Assignment.objects.none()
    )
    submissions_map = {
        sub.assignment_id: sub
        for sub in AssignmentSubmission.objects.filter(student=user)
    }

    att_records = AttendanceRecord.objects.filter(student=user)
    total_att = att_records.count()
    present_att = att_records.filter(status=AttendanceRecord.STATUS_PRESENT).count()
    overall_attendance_pct = round((present_att / total_att) * 100.0, 1) if total_att else 0.0

    context = {
        'student_class': student_class,
        'exams': exams[:6],
        'attempts_map': attempts_map,
        'assignments': assignments[:6],
        'submissions_map': submissions_map,
        'total_att': total_att,
        'present_att': present_att,
        'overall_attendance_pct': overall_attendance_pct,
        'recent_notices': recent_notices,
        'read_ids': read_ids,
        'unread_count': get_unread_message_count(user),
    }
    return render(request, 'dashboard/student_dashboard.html', context)


# ---------------------------------------------------------------------------
# Super Admin: Bulk Excel Onboarding, Printable Roster, Users & Classes
# ---------------------------------------------------------------------------

def _normalize_excel_columns(headers):
    """Map flexible column header indices to canonical keys."""
    col_map = {}
    for idx, col in enumerate(headers):
        key = str(col or '').strip().lower()
        if any(k in key for k in ('roll', 'system id', 'id')):
            col_map['roll_number'] = idx
        elif 'name' in key:
            col_map['full_name'] = idx
        elif any(k in key for k in ('class', 'dept', 'department', 'program', 'course')):
            col_map['class_name'] = idx
        elif 'role' in key:
            col_map['role'] = idx
        elif 'sem' in key:
            col_map['semester'] = idx

    if len(col_map) < 4 and len(headers) >= 4:
        col_map = {
            'roll_number': 0,
            'full_name': 1,
            'class_name': 2,
            'role': 3,
        }
        if len(headers) >= 5:
            col_map['semester'] = 4
    return col_map


def _parse_role(raw_role):
    val = str(raw_role).strip().upper()
    if val in ('ADMIN', 'SUPER ADMIN', 'SUPERADMIN', 'ADMINISTRATOR'):
        return CustomUser.ROLE_ADMIN
    if val in ('TEACHER', 'FACULTY', 'PROFESSOR', 'INSTRUCTOR', 'STAFF'):
        return CustomUser.ROLE_TEACHER
    return CustomUser.ROLE_STUDENT


@role_required(CustomUser.ROLE_ADMIN)
def bulk_onboarding_view(request):
    form = BulkOnboardingForm(request.POST or None, request.FILES or None)
    upload_summary = None

    if request.method == 'POST' and form.is_valid():
        excel_file = form.cleaned_data['excel_file']
        try:
            wb = openpyxl.load_workbook(excel_file, data_only=True)
            ws = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
        except Exception as exc:
            messages.error(request, f'Could not read Excel file: {exc}')
            return redirect('bulk_onboarding')

        if len(all_rows) < 2:
            messages.error(request, 'The uploaded Excel sheet has no data rows.')
            return redirect('bulk_onboarding')

        headers = [str(cell).strip() if cell is not None else '' for cell in all_rows[0]]
        data_rows = all_rows[1:]

        col_map = _normalize_excel_columns(headers)
        required_keys = {'roll_number', 'full_name', 'class_name', 'role'}
        if not required_keys.issubset(col_map.keys()):
            messages.error(
                request,
                'Excel must contain columns: Roll Number, Name, Class/Department, and Role.',
            )
            return redirect('bulk_onboarding')

        created_count = 0
        updated_count = 0
        skipped_count = 0
        created_classes = []
        credentials_rows = []

        def _cell_str(row_tuple, col_idx):
            if col_idx is None or col_idx >= len(row_tuple) or row_tuple[col_idx] is None:
                return ''
            val = row_tuple[col_idx]
            if isinstance(val, float) and val.is_integer():
                return str(int(val))
            return str(val).strip()

        with transaction.atomic():
            for row in data_rows:
                roll = _cell_str(row, col_map['roll_number']).upper()
                name = _cell_str(row, col_map['full_name'])
                class_name = _cell_str(row, col_map['class_name'])
                role_raw = _cell_str(row, col_map['role'])
                sem_raw = _cell_str(row, col_map.get('semester'))

                if not roll or roll == 'NAN':
                    skipped_count += 1
                    continue

                if not name:
                    name = roll

                role = _parse_role(role_raw)
                academic_class = None

                if class_name and class_name.upper() not in ('NAN', 'ALL', 'NONE', '-'):
                    academic_class = AcademicClass.objects.filter(name__iexact=class_name).first()
                    if not academic_class:
                        base_code = re.sub(r'[^A-Z0-9]+', '', class_name.upper())[:15] or 'CLS'
                        code = base_code
                        suffix = 1
                        while AcademicClass.objects.filter(code=code).exists():
                            code = f"{base_code}{suffix}"
                            suffix += 1
                        academic_class = AcademicClass.objects.create(
                            name=class_name,
                            code=code,
                            description=f'Auto-created during bulk onboarding for {class_name}',
                        )
                        created_classes.append(academic_class.name)

                default_pwd = f"{roll}@2026"
                defaults = {
                    'full_name': name,
                    'role': role,
                    'student_class': academic_class if role == CustomUser.ROLE_STUDENT else None,
                    'must_change_password': True,
                    'is_staff': role == CustomUser.ROLE_ADMIN,
                }
                if hasattr(CustomUser, 'semester') and sem_raw:
                    defaults['semester'] = sem_raw

                user, created = CustomUser.objects.get_or_create(
                    roll_number=roll,
                    defaults=defaults,
                )
                if created:
                    user.set_password(default_pwd)
                    user.save()
                    if role == CustomUser.ROLE_TEACHER and academic_class:
                        user.assigned_classes.add(academic_class)
                    created_count += 1
                    status_label = 'Created'
                else:
                    user.full_name = name
                    user.role = role
                    if hasattr(user, 'semester') and sem_raw:
                        user.semester = sem_raw
                    if role == CustomUser.ROLE_STUDENT and academic_class:
                        user.student_class = academic_class
                    elif role == CustomUser.ROLE_TEACHER and academic_class:
                        user.assigned_classes.add(academic_class)
                    user.save()
                    updated_count += 1
                    status_label = 'Updated (Existing Password Kept)'

                credentials_rows.append({
                    'roll_number': user.roll_number,
                    'full_name': user.full_name,
                    'class_name': academic_class.name if academic_class else (user.student_class.name if user.student_class else 'General'),
                    'role': user.get_role_display(),
                    'default_password': default_pwd if created else (default_pwd if user.must_change_password else 'Private Password Active'),
                    'status': status_label,
                })

        request.session['last_onboarding_credentials'] = credentials_rows
        upload_summary = {
            'created_count': created_count,
            'updated_count': updated_count,
            'skipped_count': skipped_count,
            'created_classes': created_classes,
            'rows': credentials_rows,
        }
        messages.success(
            request,
            f'Bulk onboarding complete: {created_count} created, {updated_count} updated, {len(created_classes)} new classes auto-created.',
        )

    return render(request, 'admin_panel/bulk_onboarding.html', {
        'form': form,
        'upload_summary': upload_summary,
        'has_batch_credentials': bool(request.session.get('last_onboarding_credentials')),
    })


@role_required(CustomUser.ROLE_ADMIN)
def download_sample_excel(request):
    """Provide a clean 5-column .xlsx template supporting multi-class Teachers & Sem-wise Students."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Onboarding Template'
    headers = ['Roll Number', 'Name', 'Class/Department', 'Role', 'Semester']
    ws.append(headers)

    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)
    for col_idx in range(1, 6):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    sample_rows = [
        ['BBA101', 'Aarav Mehta', 'BBA', 'Student', 'Sem 1'],
        ['BBA201', 'Diya Sharma', 'BBA', 'Student', 'Sem 2'],
        ['BCOM301', 'Rohan Verma', 'BCom', 'Student', 'Sem 3'],
        ['BCOM401', 'Sneha Patel', 'BCom', 'Student', 'Sem 4'],
        ['TCH101', 'Prof. Kavita Nair', 'BBA, BCom', 'Teacher', ''],
        ['TCH102', 'Prof. Arvind Joshi', 'BBA & BCom', 'Teacher', ''],
    ]
    for r in sample_rows:
        ws.append(r)

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max(18, max_len + 4)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="lms_bulk_onboarding_template.xlsx"'
    return response


@role_required(CustomUser.ROLE_ADMIN)
def download_credentials_excel(request):
    """Download Credentials Excel (.xlsx) with <RollNumber>@2026 default passwords."""
    scope = request.GET.get('scope', 'batch')
    rows = request.session.get('last_onboarding_credentials')

    if scope == 'all' or not rows:
        rows = []
        for u in CustomUser.objects.select_related('student_class').prefetch_related('assigned_classes').all():
            if u.student_class:
                cls_name = u.student_class.name
            elif u.assigned_classes.exists():
                cls_name = ', '.join(c.name for c in u.assigned_classes.all())
            else:
                cls_name = 'Administration'
            rows.append({
                'roll_number': u.roll_number,
                'full_name': u.full_name,
                'class_name': cls_name,
                'semester': u.semester or '—',
                'role': u.get_role_display(),
                'default_password': u.default_password if u.must_change_password else 'Private Password Set',
                'status': 'First Login Pending' if u.must_change_password else 'Active',
            })

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'LMS Credentials'
    ws.append(['Roll Number / System ID', 'Full Name', 'Class / Department', 'Semester', 'Role', 'Login Password', 'Account Status'])

    header_fill = PatternFill(start_color='0F172A', end_color='0F172A', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)
    for col_idx in range(1, 8):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for r in rows:
        ws.append([
            r['roll_number'],
            r['full_name'],
            r['class_name'],
            r.get('semester', '—'),
            r['role'],
            r['default_password'],
            r['status'],
        ])

    for col in ws.columns:
        max_len = max(len(str(cell.value or '')) for cell in col)
        ws.column_dimensions[col[0].column_letter].width = max(16, max_len + 4)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="lms_user_credentials.xlsx"'
    return response


@role_required(CustomUser.ROLE_ADMIN)
def printable_roster_view(request):
    class_id = request.GET.get('class_id', '')
    role_filter = request.GET.get('role', '')
    semester_filter = request.GET.get('semester', '')
    users = CustomUser.objects.select_related('student_class').prefetch_related('assigned_classes').all()

    if class_id:
        users = users.filter(Q(student_class_id=class_id) | Q(assigned_classes__id=class_id)).distinct()
    if role_filter:
        users = users.filter(role=role_filter)
    if semester_filter:
        users = users.filter(semester=semester_filter)

    return render(request, 'admin_panel/printable_roster.html', {
        'users': users,
        'classes': AcademicClass.objects.all(),
        'semester_choices': CustomUser.SEMESTER_CHOICES[1:],
        'selected_class_id': class_id,
        'selected_role': role_filter,
        'selected_semester': semester_filter,
        'generated_at': timezone.now(),
    })


@role_required(CustomUser.ROLE_ADMIN)
def manage_users_view(request):
    role_filter = request.GET.get('role', '')
    class_filter = request.GET.get('class_id', '')
    semester_filter = request.GET.get('semester', '')
    search_q = request.GET.get('q', '').strip()

    users = CustomUser.objects.select_related('student_class').prefetch_related('assigned_classes').all()
    if role_filter:
        users = users.filter(role=role_filter)
    if class_filter:
        users = users.filter(Q(student_class_id=class_filter) | Q(assigned_classes__id=class_filter)).distinct()
    if semester_filter:
        users = users.filter(semester=semester_filter)
    if search_q:
        users = users.filter(Q(roll_number__icontains=search_q) | Q(full_name__icontains=search_q))

    form = UserCreateForm(request.POST or None)
    show_create_modal = False
    if request.method == 'POST':
        if form.is_valid():
            new_user = form.save(commit=False)
            new_user.roll_number = new_user.roll_number.strip().upper()
            new_user.must_change_password = True
            new_user.is_staff = new_user.role == CustomUser.ROLE_ADMIN
            if new_user.role != CustomUser.ROLE_STUDENT:
                new_user.student_class = None
                new_user.semester = ''
            new_user.set_password(f"{new_user.roll_number}@2026")
            new_user.save()
            if new_user.role == CustomUser.ROLE_TEACHER:
                form.save_m2m()
            messages.success(
                request,
                f'{new_user.get_role_display()} {new_user.roll_number} ({new_user.full_name}) created with default password {new_user.roll_number}@2026.',
            )
            return redirect('manage_users')
        else:
            show_create_modal = True
            messages.error(request, 'Could not create user. Please check the highlighted fields.')

    return render(request, 'admin_panel/manage_users.html', {
        'users': users,
        'form': form,
        'classes': AcademicClass.objects.all(),
        'semester_choices': CustomUser.SEMESTER_CHOICES[1:],
        'role_filter': role_filter,
        'class_filter': class_filter,
        'semester_filter': semester_filter,
        'search_q': search_q,
        'show_create_modal': show_create_modal,
    })


@role_required(CustomUser.ROLE_ADMIN)
def _user_view(request, user_id):
    """Super Admin full profile or for Students, Teachers, and Admins."""
    target = get_object_or_404(CustomUser.objects.prefetch_related('assigned_classes'), id=user_id)
    form = AdminUserForm(request.POST or None, instance=target)

    if request.method == 'POST' and form.is_valid():
        updated = form.save(commit=False)
        updated.roll_number = updated.roll_number.strip().upper()
        updated.is_staff = updated.role == CustomUser.ROLE_ADMIN
        if updated.role != CustomUser.ROLE_STUDENT:
            updated.student_class = None
            updated.semester = ''
        new_pwd = form.cleaned_data.get('new_password')
        if new_pwd:
            updated.set_password(new_pwd)
        updated.save()
        if updated.role == CustomUser.ROLE_TEACHER:
            updated.assigned_classes.set(form.cleaned_data.get('assigned_classes'))
        else:
            updated.assigned_classes.clear()

        if new_pwd and updated == request.user:
            update_session_auth_hash(request, updated)

        messages.success(request, f'Profile updated for {updated.roll_number} ({updated.full_name}).')
        return redirect('manage_users')

    return render(request, 'admin_panel/_user.html', {
        'target': target,
        'form': form,
    })


@role_required(CustomUser.ROLE_ADMIN)
@require_POST
def reset_user_password_view(request, user_id):
    target = get_object_or_404(CustomUser, id=user_id)
    default_pwd = target.default_password
    target.set_password(default_pwd)
    target.must_change_password = True
    target.save()
    messages.success(
        request,
        f'Password for {target.roll_number} reset to "{default_pwd}". User will be forced to set a new password on next login.',
    )
    return redirect('manage_users')


@role_required(CustomUser.ROLE_ADMIN)
@require_POST
def delete_user_view(request, user_id):
    target = get_object_or_404(CustomUser, id=user_id)
    if target == request.user:
        messages.error(request, 'You cannot delete your own active Super Admin account.')
        return redirect('manage_users')
    roll = target.roll_number
    target.delete()
    messages.success(request, f'User {roll} permanently deleted.')
    return redirect('manage_users')


@role_required(CustomUser.ROLE_ADMIN)
def manage_classes_view(request):
    class_form = AcademicClassForm(prefix='cls')
    subject_form = SubjectForm(prefix='sub')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_class':
            class_form = AcademicClassForm(request.POST, prefix='cls')
            if class_form.is_valid():
                cls_obj = class_form.save()
                messages.success(request, f'Class "{cls_obj.name}" created.')
                return redirect('manage_classes')
        elif action == 'create_subject':
            subject_form = SubjectForm(request.POST, prefix='sub')
            if subject_form.is_valid():
                sub_obj = subject_form.save()
                messages.success(request, f'Subject "{sub_obj.name}" added to {sub_obj.academic_class.name}.')
                return redirect('manage_classes')

    classes = AcademicClass.objects.annotate(
        student_count=Count('students', distinct=True),
        subject_count=Count('subjects', distinct=True),
    )
    subjects = Subject.objects.select_related('academic_class', 'teacher').all()

    return render(request, 'admin_panel/manage_classes.html', {
        'classes': classes,
        'subjects': subjects,
        'class_form': class_form,
        'subject_form': subject_form,
    })


@role_required(CustomUser.ROLE_ADMIN)
@require_POST
def delete_class_view(request, class_id):
    cls_obj = get_object_or_404(AcademicClass, id=class_id)
    name = cls_obj.name
    cls_obj.delete()
    messages.success(request, f'Class "{name}" and its linked subjects/exams were deleted.')
    return redirect('manage_classes')


@role_required(CustomUser.ROLE_ADMIN)
@require_POST
def delete_subject_view(request, subject_id):
    sub = get_object_or_404(Subject, id=subject_id)
    name = sub.name
    sub.delete()
    messages.success(request, f'Subject "{name}" deleted.')
    return redirect('manage_classes')


# ---------------------------------------------------------------------------
# Question Bank, Printable Question Paper & Moodle Exam Engine
# ---------------------------------------------------------------------------

@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def question_bank_view(request):
    subject_id = request.GET.get('subject_id', '')
    qtype = request.GET.get('type', '')
    search_q = request.GET.get('q', '').strip()

    questions = Question.objects.select_related('subject', 'subject__academic_class', 'created_by').all()
    if subject_id:
        questions = questions.filter(subject_id=subject_id)
    if qtype:
        questions = questions.filter(question_type=qtype)
    if search_q:
        questions = questions.filter(prompt__icontains=search_q)

    return render(request, 'exams/question_bank.html', {
        'questions': questions,
        'subjects': Subject.objects.select_related('academic_class').all(),
        'selected_subject_id': subject_id,
        'selected_type': qtype,
        'search_q': search_q,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def question_create_view(request):
    form = QuestionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        question = form.save(commit=False)
        question.created_by = request.user
        question.save()
        messages.success(request, 'Question added to the Question Bank.')
        return redirect('question_bank')
    return render(request, 'exams/question_form.html', {'form': form, 'is_': False})


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def question__view(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    form = QuestionForm(request.POST or None, instance=question)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Question #{question.id} updated.')
        return redirect('question_bank')
    return render(request, 'exams/question_form.html', {'form': form, 'is_': True, 'question': question})


@role_required(CustomUser.ROLE_ADMIN)
@require_POST
def question_delete_view(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    question.delete()
    messages.success(request, f'Question #{question_id} deleted.')
    return redirect('question_bank')


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def question_import_pdf_view(request):
    """Upload a PDF, automatically recognize questions + math symbols, preview/, and bulk-save."""
    if request.GET.get('clear') == '1':
        for k in ('pdf_extracted_questions', 'pdf_import_subject_id', 'pdf_import_engine', 'pdf_import_filename'):
            request.session.pop(k, None)
        return redirect('question_import_pdf')

    form = PdfQuestionUploadForm(request.POST or None, request.FILES or None)

    if request.method == 'POST' and request.POST.get('save_selected') == '1':
        subject_id = request.POST.get('subject_id') or request.session.get('pdf_import_subject_id')
        subject = get_object_or_404(Subject, id=subject_id)

        rows_to_save = []
        raw_payload = request.POST.get('questions_payload', '').strip()
        if raw_payload:
            try:
                parsed_rows = json.loads(raw_payload)
                if isinstance(parsed_rows, list):
                    rows_to_save = parsed_rows
            except (ValueError, TypeError):
                rows_to_save = []
        else:
            try:
                row_count = int(request.POST.get('row_count', 0))
            except (TypeError, ValueError):
                row_count = 0
            for i in range(row_count):
                if request.POST.get(f'include_{i}') != '1':
                    continue
                rows_to_save.append({
                    'prompt': request.POST.get(f'prompt_{i}') or '',
                    'question_type': request.POST.get(f'question_type_{i}') or Question.TYPE_SHORT,
                    'marks': request.POST.get(f'marks_{i}', 1),
                    'difficulty': request.POST.get(f'difficulty_{i}') or Question.DIFF_MEDIUM,
                    'correct_option': request.POST.get(f'correct_option_{i}') or '',
                    'option_a': request.POST.get(f'option_a_{i}') or '',
                    'option_b': request.POST.get(f'option_b_{i}') or '',
                    'option_c': request.POST.get(f'option_c_{i}') or '',
                    'option_d': request.POST.get(f'option_d_{i}') or '',
                    'correct_answer_text': request.POST.get(f'correct_answer_text_{i}') or '',
                    'model_answer': request.POST.get(f'model_answer_{i}') or '',
                })

        questions_to_create = []
        for row in rows_to_save:
            prompt = str(row.get('prompt') or '').strip()
            if not prompt:
                continue
            qtype = str(row.get('question_type') or Question.TYPE_SHORT).strip().upper()
            if qtype not in (Question.TYPE_MCQ, Question.TYPE_FITB, Question.TYPE_SHORT):
                qtype = Question.TYPE_SHORT
            try:
                marks = max(1, int(row.get('marks', 1)))
            except (TypeError, ValueError):
                marks = 1
            diff = str(row.get('difficulty') or Question.DIFF_MEDIUM).strip().upper()
            if diff not in (Question.DIFF_EASY, Question.DIFF_MEDIUM, Question.DIFF_HARD):
                diff = Question.DIFF_MEDIUM

            correct_opt = str(row.get('correct_option') or '').strip().upper()[:1]
            if correct_opt not in ('A', 'B', 'C', 'D'):
                correct_opt = 'A' if qtype == Question.TYPE_MCQ else ''

            questions_to_create.append(Question(
                subject=subject,
                created_by=request.user,
                question_type=qtype,
                prompt=prompt,
                option_a=str(row.get('option_a') or '').strip(),
                option_b=str(row.get('option_b') or '').strip(),
                option_c=str(row.get('option_c') or '').strip(),
                option_d=str(row.get('option_d') or '').strip(),
                correct_option=correct_opt,
                correct_answer_text=str(row.get('correct_answer_text') or '').strip(),
                model_answer=str(row.get('model_answer') or '').strip(),
                marks=marks,
                difficulty=diff,
            ))

        with transaction.atomic():
            if questions_to_create:
                Question.objects.bulk_create(questions_to_create)

        saved_count = len(questions_to_create)
        for k in ('pdf_extracted_questions', 'pdf_import_subject_id', 'pdf_import_engine', 'pdf_import_filename'):
            request.session.pop(k, None)

        if saved_count:
            messages.success(request, f'Successfully imported {saved_count} questions (with math symbols) into "{subject.name}".')
            return redirect('question_bank')
        messages.warning(request, 'No questions were selected to import.')
        return redirect('question_import_pdf')

    if request.method == 'POST' and form.is_valid():
        subject = form.cleaned_data['subject']
        pdf_file = form.cleaned_data['pdf_file']
        mode = form.cleaned_data['extraction_mode']
        try:
            extracted, engine_used = extract_questions_from_pdf(pdf_file, mode=mode)
        except Exception as exc:
            messages.error(request, f'PDF Question Recognition Error: {exc}')
            extracted = []
            engine_used = ''

        if extracted:
            request.session['pdf_extracted_questions'] = extracted
            request.session['pdf_import_subject_id'] = subject.id
            request.session['pdf_import_engine'] = engine_used
            request.session['pdf_import_filename'] = pdf_file.name
            messages.success(
                request,
                f'Recognized {len(extracted)} questions from "{pdf_file.name}" using {engine_used}. Review and save below.',
            )
        elif engine_used or not messages.get_messages(request):
            messages.warning(
                request,
                'No selectable text/questions were detected in this PDF. Try Gemini AI mode for scanned/image PDFs.',
            )

    extracted_questions = request.session.get('pdf_extracted_questions', [])
    preview_subject_id = request.session.get('pdf_import_subject_id')
    preview_subject = Subject.objects.select_related('academic_class').filter(id=preview_subject_id).first() if preview_subject_id else None

    return render(request, 'exams/question_import_pdf.html', {
        'form': form,
        'extracted_questions': extracted_questions,
        'preview_subject': preview_subject,
        'engine_used': request.session.get('pdf_import_engine', ''),
        'source_filename': request.session.get('pdf_import_filename', ''),
    })


@login_required
def exam_list_view(request):
    user = request.user
    if user.is_student:
        exams = (
            Exam.objects.filter(academic_class=user.student_class, is_published=True)
            .select_related('academic_class', 'subject', 'created_by')
            if user.student_class else Exam.objects.none()
        )
        attempts_map = {
            a.exam_id: a for a in ExamAttempt.objects.filter(student=user)
        }
        return render(request, 'exams/exam_list.html', {
            'exams': exams,
            'attempts_map': attempts_map,
        })

    exams = Exam.objects.select_related('academic_class', 'subject', 'created_by').annotate(
        attempt_count=Count('attempts', distinct=True)
    )
    return render(request, 'exams/exam_list.html', {'exams': exams})


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def exam_create_view(request):
    form = ExamForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            exam = form.save(commit=False)
            exam.created_by = request.user
            exam.save()
            for idx, question in enumerate(form.cleaned_data['selected_questions'], start=1):
                ExamQuestion.objects.create(exam=exam, question=question, order=idx)
        messages.success(request, f'Exam "{exam.title}" created with {exam.question_count} questions.')
        return redirect('exam_list')

    return render(request, 'exams/exam_form.html', {'form': form, 'is_': False})


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def exam__view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    initial_q_ids = list(exam.exam_questions.values_list('question_id', flat=True))
    form = ExamForm(request.POST or None, instance=exam, initial={'selected_questions': initial_q_ids})

    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            exam = form.save()
            exam.exam_questions.all().delete()
            for idx, question in enumerate(form.cleaned_data['selected_questions'], start=1):
                ExamQuestion.objects.create(exam=exam, question=question, order=idx)
        messages.success(request, f'Exam "{exam.title}" updated.')
        return redirect('exam_list')

    return render(request, 'exams/exam_form.html', {'form': form, 'is_': True, 'exam': exam})


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def printable_paper_view(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related('academic_class', 'subject'), id=exam_id)
    exam_questions = exam.exam_questions.select_related('question').all()
    show_key = request.GET.get('answer_key', '0') == '1'
    return render(request, 'exams/printable_paper.html', {
        'exam': exam,
        'exam_questions': exam_questions,
        'show_key': show_key,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def format_paper_generator_view(request, exam_id=None):
    """Upload a custom paper format (.docx/.pdf/.txt) and generate matching Word & PDF papers."""
    exams_qs = Exam.objects.select_related('academic_class', 'subject').all()
    selected_exam = None
    if exam_id:
        selected_exam = get_object_or_404(exams_qs, id=exam_id)
    elif request.session.get('paper_format_exam_id'):
        selected_exam = exams_qs.filter(id=request.session.get('paper_format_exam_id')).first()
    if not selected_exam:
        selected_exam = exams_qs.first()

    initial = {}
    if selected_exam:
        initial['exam'] = selected_exam.id
    initial['include_answer_key'] = bool(request.session.get('paper_format_answer_key', False))

    form = PaperFormatUploadForm(request.POST or None, request.FILES or None, initial=initial)

    if request.method == 'POST' and form.is_valid():
        selected_exam = form.cleaned_data['exam']
        fmt_file = form.cleaned_data['format_file']
        include_key = bool(form.cleaned_data.get('include_answer_key', False))
        try:
            raw_bytes = fmt_file.read()
            fmt_file.seek(0)
            fmt_config = parse_paper_format(fmt_file)
            request.session['paper_format_config'] = fmt_config
            request.session['paper_format_exam_id'] = selected_exam.id
            request.session['paper_format_answer_key'] = include_key
            if fmt_config.get('is_docx_template') and len(raw_bytes) <= 1_500_000:
                request.session['paper_format_docx_b64'] = base64.b64encode(raw_bytes).decode('ascii')
            else:
                request.session.pop('paper_format_docx_b64', None)
            messages.success(
                request,
                f'Paper format "{fmt_file.name}" parsed ({len(fmt_config.get("sections", []))} sections detected). Download Word or PDF below.',
            )
        except Exception as exc:
            messages.error(request, f'Could not parse uploaded paper format: {exc}')

    fmt_config = request.session.get('paper_format_config')
    include_key = bool(request.session.get('paper_format_answer_key', False))

    return render(request, 'exams/format_paper_generator.html', {
        'form': form,
        'selected_exam': selected_exam,
        'exams': exams_qs,
        'fmt_config': fmt_config,
        'include_answer_key': include_key,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def download_sample_format_docx(request):
    docx_bytes = build_sample_format_docx()
    response = HttpResponse(
        docx_bytes,
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )
    response['Content-Disposition'] = 'attachment; filename="acadex_sample_paper_format.docx"'
    return response


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def download_formatted_paper(request, exam_id, output_format):
    exam = get_object_or_404(Exam.objects.select_related('academic_class', 'subject'), id=exam_id)
    fmt_config = request.session.get('paper_format_config') or {
        'source_filename': 'Default Acadex Paper Format',
        'institution': 'ACADEX INSTITUTION OF MANAGEMENT & COMMERCE',
        'subtitle': f'{exam.academic_class.name} Examination Paper',
        'instructions': [exam.instructions] if exam.instructions else [],
        'sections': [
            {'title': 'SECTION A — MULTIPLE CHOICE QUESTIONS', 'qtype': 'MCQ'},
            {'title': 'SECTION B — FILL IN THE BLANKS', 'qtype': 'FITB'},
            {'title': 'SECTION C — SHORT ANSWER QUESTIONS', 'qtype': 'SHORT'},
        ],
        'footer_note': '*** END OF QUESTION PAPER ***',
        'is_docx_template': False,
        'has_placeholders': False,
    }

    ans_param = request.GET.get('answer_key')
    if ans_param in ('1', 'true', 'yes'):
        include_key = True
    elif ans_param in ('0', 'false', 'no'):
        include_key = False
    else:
        include_key = bool(request.session.get('paper_format_answer_key', False))

    template_docx_bytes = None
    b64_data = request.session.get('paper_format_docx_b64')
    if b64_data and fmt_config.get('is_docx_template'):
        try:
            template_docx_bytes = base64.b64decode(b64_data)
        except Exception:
            template_docx_bytes = None

    safe_slug = re.sub(r'[^A-Za-z0-9_-]+', '_', exam.title).strip('_') or f'exam_{exam.id}'
    fmt_lower = output_format.lower()

    if fmt_lower in ('docx', 'word'):
        docx_bytes = generate_word_paper(
            exam, fmt_config, include_answer_key=include_key, template_docx_bytes=template_docx_bytes
        )
        response = HttpResponse(
            docx_bytes,
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        response['Content-Disposition'] = f'attachment; filename="{safe_slug}_question_paper.docx"'
        return response

    if fmt_lower == 'pdf':
        pdf_bytes = generate_pdf_paper(exam, fmt_config, include_answer_key=include_key)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{safe_slug}_question_paper.pdf"'
        return response

    if fmt_lower == 'both':
        docx_bytes = generate_word_paper(
            exam, fmt_config, include_answer_key=include_key, template_docx_bytes=template_docx_bytes
        )
        pdf_bytes = generate_pdf_paper(exam, fmt_config, include_answer_key=include_key)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{safe_slug}_question_paper.docx', docx_bytes)
            zf.writestr(f'{safe_slug}_question_paper.pdf', pdf_bytes)
        zip_buf.seek(0)
        response = HttpResponse(zip_buf.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{safe_slug}_word_and_pdf_papers.zip"'
        return response

    messages.error(request, 'Unsupported download format requested.')
    return redirect('format_paper_generator_for_exam', exam_id=exam.id)


def _has_session_conflict(request, attempt):
    session_key = _ensure_session_key(request)
    if attempt.active_session_key and attempt.active_session_key != session_key:
        return True
    if request.user.active_session_key and request.user.active_session_key != session_key:
        return True
    return False


@role_required(CustomUser.ROLE_STUDENT)
def take_exam_view(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related('academic_class', 'subject'), id=exam_id, is_published=True)
    if request.user.student_class_id != exam.academic_class_id:
        messages.error(request, 'This exam is not assigned to your class.')
        return redirect('exam_list')

    session_key = _ensure_session_key(request)
    attempt, created = ExamAttempt.objects.get_or_create(
        exam=exam,
        student=request.user,
        defaults={
            'status': ExamAttempt.STATUS_IN_PROGRESS,
            'active_session_key': session_key,
        },
    )

    if attempt.status != ExamAttempt.STATUS_IN_PROGRESS:
        return redirect('exam_result', attempt_id=attempt.id)

    if not attempt.active_session_key:
        attempt.active_session_key = session_key
        attempt.save(update_fields=['active_session_key'])

    if not request.user.active_session_key:
        request.user.active_session_key = session_key
        request.user.save(update_fields=['active_session_key'])

    if _has_session_conflict(request, attempt):
        _terminate_attempt_for_second_device(
            attempt,
            'Exam auto-submitted & closed: Account accessed from a second phone/device.',
        )
        messages.error(
            request,
            'Security Alert: Your exam was auto-submitted and closed because another phone/device accessed this account.',
        )
        return redirect('exam_result', attempt_id=attempt.id)

    remaining_secs = attempt.remaining_seconds()
    if remaining_secs <= 0:
        attempt.evaluate_attempt(auto_submitted=True)
        messages.warning(request, 'Time expired! Your exam was automatically submitted.')
        return redirect('exam_result', attempt_id=attempt.id)

    # Establish deterministic question order for this attempt
    if not attempt.question_order:
        q_ids = list(exam.exam_questions.values_list('question_id', flat=True))
        if exam.shuffle_questions:
            random.shuffle(q_ids)
        attempt.question_order = q_ids
        attempt.save(update_fields=['question_order'])

    q_map = {q.id: q for q in Question.objects.filter(id__in=attempt.question_order)}
    ordered_questions = [q_map[qid] for qid in attempt.question_order if qid in q_map]

    # Ensure ExamAnswer objects exist for all questions
    existing_answers = {a.question_id: a for a in attempt.answers.all()}
    question_items = []
    for idx, q in enumerate(ordered_questions, start=1):
        ans = existing_answers.get(q.id)
        if ans is None:
            ans = ExamAnswer.objects.create(attempt=attempt, question=q, is_visited=(idx == 1))
        question_items.append({
            'number': idx,
            'question': q,
            'answer': ans,
        })

    return render(request, 'exams/take_exam.html', {
        'exam': exam,
        'attempt': attempt,
        'question_items': question_items,
        'remaining_seconds': remaining_secs,
        'exam_fullscreen_mode': True,
    })


@role_required(CustomUser.ROLE_STUDENT)
@require_POST
def save_exam_answer_api(request, attempt_id):
    attempt = get_object_or_404(ExamAttempt, id=attempt_id, student=request.user)
    result_url = reverse('exam_result', args=[attempt.id])

    if attempt.status != ExamAttempt.STATUS_IN_PROGRESS:
        return JsonResponse({
            'ok': False,
            'terminated': True,
            'reason': attempt.termination_reason or 'Attempt already submitted.',
            'redirect_url': result_url,
        }, status=400)

    if _has_session_conflict(request, attempt):
        _terminate_attempt_for_second_device(
            attempt,
            'Exam auto-submitted & closed: Another phone/device logged in while answering.',
        )
        return JsonResponse({
            'ok': False,
            'terminated': True,
            'auto_submitted': True,
            'reason': attempt.termination_reason,
            'redirect_url': result_url,
        })

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        payload = request.POST

    question_id = payload.get('question_id')
    selected_option = (payload.get('selected_option') or '').strip().upper()
    text_answer = payload.get('text_answer') or ''
    is_marked = bool(payload.get('is_marked_for_review', False))
    is_visited = bool(payload.get('is_visited', True))

    ans, _ = ExamAnswer.objects.get_or_create(attempt=attempt, question_id=question_id)
    ans.selected_option = selected_option
    ans.text_answer = text_answer
    ans.is_marked_for_review = is_marked
    ans.is_visited = is_visited
    ans.save()

    return JsonResponse({
        'ok': True,
        'terminated': False,
        'remaining_seconds': attempt.remaining_seconds(),
    })


@role_required(CustomUser.ROLE_STUDENT)
@require_POST
def log_tab_switch_api(request, attempt_id):
    attempt = get_object_or_404(ExamAttempt, id=attempt_id, student=request.user)
    result_url = reverse('exam_result', args=[attempt.id])

    if attempt.status != ExamAttempt.STATUS_IN_PROGRESS:
        return JsonResponse({
            'ok': True,
            'auto_submitted': True,
            'terminated': True,
            'reason': attempt.termination_reason or 'Attempt already submitted.',
            'redirect_url': result_url,
            'count': attempt.tab_switch_count,
        })

    if _has_session_conflict(request, attempt):
        _terminate_attempt_for_second_device(
            attempt,
            'Exam auto-submitted & closed: Another phone/device logged in.',
        )
        return JsonResponse({
            'ok': True,
            'auto_submitted': True,
            'terminated': True,
            'reason': attempt.termination_reason,
            'redirect_url': result_url,
            'count': attempt.tab_switch_count,
        })

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except Exception:
        payload = {}

    reason = payload.get('reason', 'Tab switched / Window lost focus')
    attempt.tab_switch_count += 1
    log = list(attempt.tab_switch_log or [])
    log.append({
        'count': attempt.tab_switch_count,
        'reason': reason,
        'timestamp': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    attempt.tab_switch_log = log
    attempt.save(update_fields=['tab_switch_count', 'tab_switch_log'])

    auto_submit = attempt.tab_switch_count >= attempt.exam.max_tab_switches
    if auto_submit:
        attempt.termination_reason = f'Auto-submitted: Maximum anti-cheat violations ({attempt.exam.max_tab_switches}) reached.'
        attempt.evaluate_attempt(auto_submitted=True)

    return JsonResponse({
        'ok': True,
        'count': attempt.tab_switch_count,
        'max_tab_switches': attempt.exam.max_tab_switches,
        'auto_submitted': auto_submit,
        'terminated': auto_submit,
        'reason': attempt.termination_reason,
        'redirect_url': result_url,
    })


@role_required(CustomUser.ROLE_STUDENT)
def exam_heartbeat_api(request, attempt_id):
    """Polled by active exam UI to enforce single-device lock and instant auto-submit/close."""
    attempt = get_object_or_404(ExamAttempt.objects.select_related('student', 'exam'), id=attempt_id, student=request.user)
    result_url = reverse('exam_result', args=[attempt.id])

    if attempt.status != ExamAttempt.STATUS_IN_PROGRESS:
        return JsonResponse({
            'ok': True,
            'terminated': True,
            'reason': attempt.termination_reason or 'Exam already submitted (including via another device).',
            'remaining_seconds': 0,
            'redirect_url': result_url,
        })

    if _has_session_conflict(request, attempt):
        _terminate_attempt_for_second_device(
            attempt,
            'Exam auto-submitted & closed: Account logged in from another phone/device.',
        )
        return JsonResponse({
            'ok': True,
            'terminated': True,
            'reason': attempt.termination_reason,
            'remaining_seconds': 0,
            'redirect_url': result_url,
        })

    rem = attempt.remaining_seconds()
    if rem <= 0:
        attempt.termination_reason = 'Exam auto-submitted: Countdown timer reached zero.'
        attempt.evaluate_attempt(auto_submitted=True)
        return JsonResponse({
            'ok': True,
            'terminated': True,
            'reason': attempt.termination_reason,
            'remaining_seconds': 0,
            'redirect_url': result_url,
        })

    return JsonResponse({
        'ok': True,
        'terminated': False,
        'reason': '',
        'remaining_seconds': rem,
        'redirect_url': result_url,
    })


@role_required(CustomUser.ROLE_STUDENT)
@require_POST
def submit_exam_view(request, attempt_id):
    attempt = get_object_or_404(ExamAttempt, id=attempt_id, student=request.user)
    if attempt.status == ExamAttempt.STATUS_IN_PROGRESS:
        # Persist any posted form fields before final evaluation
        for key, value in request.POST.items():
            if key.startswith('q_opt_'):
                qid = key.replace('q_opt_', '')
                ans, _ = ExamAnswer.objects.get_or_create(attempt=attempt, question_id=qid)
                ans.selected_option = value.strip().upper()
                ans.is_visited = True
                ans.save()
            elif key.startswith('q_txt_'):
                qid = key.replace('q_txt_', '')
                ans, _ = ExamAnswer.objects.get_or_create(attempt=attempt, question_id=qid)
                ans.text_answer = value
                ans.is_visited = True
                ans.save()

        auto_flag = request.POST.get('auto_submitted') == '1'
        attempt.evaluate_attempt(auto_submitted=auto_flag)
        if auto_flag:
            messages.warning(request, 'Exam was auto-submitted.')
        else:
            messages.success(request, 'Exam submitted successfully! Review your results below.')

    return redirect('exam_result', attempt_id=attempt.id)


@login_required
def exam_result_view(request, attempt_id):
    attempt = get_object_or_404(
        ExamAttempt.objects.select_related('exam', 'exam__subject', 'exam__academic_class', 'student'),
        id=attempt_id,
    )
    if request.user.is_student and attempt.student_id != request.user.id:
        messages.error(request, 'You can only view your own exam results.')
        return redirect('exam_list')

    answers_map = {a.question_id: a for a in attempt.answers.select_related('question').all()}
    q_ids = attempt.question_order or list(attempt.exam.exam_questions.values_list('question_id', flat=True))
    q_map = {q.id: q for q in Question.objects.filter(id__in=q_ids)}

    review_items = []
    for idx, qid in enumerate(q_ids, start=1):
        q = q_map.get(qid)
        if q:
            review_items.append({
                'number': idx,
                'question': q,
                'answer': answers_map.get(qid),
            })

    return render(request, 'exams/exam_result.html', {
        'attempt': attempt,
        'exam': attempt.exam,
        'review_items': review_items,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def exam_attempts_view(request, exam_id):
    exam = get_object_or_404(Exam.objects.select_related('academic_class', 'subject'), id=exam_id)
    attempts = exam.attempts.select_related('student').all()
    return render(request, 'exams/exam_attempts.html', {
        'exam': exam,
        'attempts': attempts,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def grade_attempt_view(request, attempt_id):
    attempt = get_object_or_404(ExamAttempt.objects.select_related('exam', 'student'), id=attempt_id)
    answers = attempt.answers.select_related('question').all()

    if request.method == 'POST':
        for ans in answers:
            if ans.question.question_type == Question.TYPE_SHORT:
                marks_val = request.POST.get(f'marks_{ans.id}')
                fb_val = request.POST.get(f'feedback_{ans.id}', '')
                if marks_val not in (None, ''):
                    try:
                        parsed_marks = max(0.0, min(float(ans.question.marks), float(marks_val)))
                    except ValueError:
                        parsed_marks = 0.0
                    ans.marks_awarded = parsed_marks
                    ans.is_correct = parsed_marks >= (ans.question.marks * 0.5)
                    ans.grader_feedback = fb_val
                    ans.save()
        attempt.evaluate_attempt(auto_submitted=(attempt.status == ExamAttempt.STATUS_AUTO_SUBMITTED))
        messages.success(request, f'Grades updated for {attempt.student.roll_number}.')
        return redirect('exam_attempts', exam_id=attempt.exam_id)

    return render(request, 'exams/grade_attempt.html', {
        'attempt': attempt,
        'answers': answers,
    })


# ---------------------------------------------------------------------------
# Assignments Module
# ---------------------------------------------------------------------------

@login_required
def assignment_list_view(request):
    user = request.user
    if user.is_student:
        assignments = (
            Assignment.objects.filter(academic_class=user.student_class)
            .select_related('academic_class', 'subject', 'created_by')
            if user.student_class else Assignment.objects.none()
        )
        submissions_map = {
            s.assignment_id: s for s in AssignmentSubmission.objects.filter(student=user)
        }
        return render(request, 'assignments/assignment_list.html', {
            'assignments': assignments,
            'submissions_map': submissions_map,
        })

    assignments = Assignment.objects.select_related('academic_class', 'subject', 'created_by').annotate(
        submission_count=Count('submissions', distinct=True)
    )
    return render(request, 'assignments/assignment_list.html', {'assignments': assignments})


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def assignment_create_view(request):
    form = AssignmentForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        assignment = form.save(commit=False)
        assignment.created_by = request.user
        assignment.save()
        messages.success(request, f'Assignment "{assignment.title}" published to {assignment.academic_class.name}.')
        return redirect('assignment_list')
    return render(request, 'assignments/assignment_form.html', {'form': form})


@login_required
def assignment_detail_view(request, assignment_id):
    assignment = get_object_or_404(
        Assignment.objects.select_related('academic_class', 'subject', 'created_by'),
        id=assignment_id,
    )
    user = request.user

    if user.is_student:
        if user.student_class_id != assignment.academic_class_id:
            messages.error(request, 'This assignment belongs to another class.')
            return redirect('assignment_list')

        existing_sub = AssignmentSubmission.objects.filter(assignment=assignment, student=user).first()
        form = AssignmentSubmissionForm(request.POST or None, request.FILES or None, instance=existing_sub)
        if request.method == 'POST' and form.is_valid():
            sub = form.save(commit=False)
            sub.assignment = assignment
            sub.student = user
            sub.save()
            messages.success(request, 'Your assignment file has been uploaded successfully.')
            return redirect('assignment_detail', assignment_id=assignment.id)

        return render(request, 'assignments/assignment_detail.html', {
            'assignment': assignment,
            'submission': existing_sub,
            'form': form,
        })

    # Teacher & Admin view all submissions
    submissions = assignment.submissions.select_related('student', 'graded_by').all()
    return render(request, 'assignments/assignment_detail.html', {
        'assignment': assignment,
        'submissions': submissions,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
@require_POST
def grade_submission_view(request, submission_id):
    sub = get_object_or_404(AssignmentSubmission.objects.select_related('assignment', 'student'), id=submission_id)
    form = AssignmentGradeForm(request.POST, instance=sub)
    if form.is_valid():
        graded = form.save(commit=False)
        if graded.marks_obtained is not None:
            graded.marks_obtained = max(0.0, min(float(sub.assignment.max_marks), float(graded.marks_obtained)))
        graded.graded_by = request.user
        graded.graded_at = timezone.now()
        graded.save()
        messages.success(request, f'Graded submission for {sub.student.roll_number}.')
    else:
        messages.error(request, 'Invalid grade input.')
    return redirect('assignment_detail', assignment_id=sub.assignment_id)


# ---------------------------------------------------------------------------
# Class Attendance System
# ---------------------------------------------------------------------------

@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def take_attendance_view(request):
    classes = AcademicClass.objects.all()
    subjects = Subject.objects.select_related('academic_class').all()
    time_slots = [slot[0] for slot in AttendanceSession.TIME_SLOT_CHOICES]

    class_id = request.POST.get('academic_class') or request.GET.get('academic_class', '')
    subject_id = request.POST.get('subject') or request.GET.get('subject', '')
    semester_filter = request.POST.get('semester') or request.GET.get('semester', '')
    date_str = request.POST.get('date') or request.GET.get('date') or timezone.localdate().isoformat()
    time_slot = request.POST.get('time_slot') or request.GET.get('time_slot') or time_slots[0]

    selected_class = AcademicClass.objects.filter(id=class_id).first() if class_id else classes.first()
    class_subjects = subjects.filter(academic_class=selected_class) if selected_class else subjects
    selected_subject = class_subjects.filter(id=subject_id).first() if subject_id else class_subjects.first()

    students = (
        CustomUser.objects.filter(role=CustomUser.ROLE_STUDENT, student_class=selected_class, is_active=True)
        .order_by('roll_number')
        if selected_class else CustomUser.objects.none()
    )
    if semester_filter:
        students = students.filter(semester=semester_filter)

    if request.method == 'POST' and request.POST.get('save_attendance') == '1':
        if not (selected_class and selected_subject):
            messages.error(request, 'Please select both a Class and a Subject.')
            return redirect('take_attendance')

        try:
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            att_date = timezone.localdate()

        with transaction.atomic():
            session, _ = AttendanceSession.objects.get_or_create(
                academic_class=selected_class,
                subject=selected_subject,
                date=att_date,
                time_slot=time_slot,
                defaults={'taken_by': request.user},
            )
            session.taken_by = request.user
            session.save(update_fields=['taken_by'])

            for stu in students:
                status = request.POST.get(f'status_{stu.id}', AttendanceRecord.STATUS_PRESENT)
                if status not in (AttendanceRecord.STATUS_PRESENT, AttendanceRecord.STATUS_ABSENT):
                    status = AttendanceRecord.STATUS_PRESENT
                remarks = request.POST.get(f'remarks_{stu.id}', '').strip()
                AttendanceRecord.objects.update_or_create(
                    session=session,
                    student=stu,
                    defaults={'status': status, 'remarks': remarks},
                )

        messages.success(
            request,
            f'Attendance saved for {selected_class.name} - {selected_subject.code} on {att_date} ({time_slot}).',
        )
        return redirect(
            f"{request.path}?academic_class={selected_class.id}&subject={selected_subject.id}&semester={semester_filter}&date={att_date.isoformat()}&time_slot={time_slot}"
        )

    # Check if session already exists so statuses can be pre-populated (default ALL PRESENT)
    existing_status = {}
    existing_remarks = {}
    if selected_class and selected_subject:
        try:
            att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            att_date = timezone.localdate()
        existing_session = AttendanceSession.objects.filter(
            academic_class=selected_class,
            subject=selected_subject,
            date=att_date,
            time_slot=time_slot,
        ).first()
        if existing_session:
            for rec in existing_session.records.all():
                existing_status[rec.student_id] = rec.status
                existing_remarks[rec.student_id] = rec.remarks

    student_rows = [
        {
            'student': stu,
            'status': existing_status.get(stu.id, AttendanceRecord.STATUS_PRESENT),
            'remarks': existing_remarks.get(stu.id, ''),
        }
        for stu in students
    ]

    return render(request, 'attendance/take_attendance.html', {
        'classes': classes,
        'subjects': class_subjects,
        'all_subjects': subjects,
        'semester_choices': CustomUser.SEMESTER_CHOICES[1:],
        'time_slots': time_slots,
        'selected_class': selected_class,
        'selected_subject': selected_subject,
        'selected_semester': semester_filter,
        'selected_date': date_str,
        'selected_time_slot': time_slot,
        'student_rows': student_rows,
    })


@role_required(CustomUser.ROLE_STUDENT)
def student_attendance_view(request):
    records = AttendanceRecord.objects.filter(student=request.user).select_related(
        'session', 'session__subject', 'session__academic_class'
    ).order_by('-session__date', '-session__time_slot')

    total = records.count()
    present = records.filter(status=AttendanceRecord.STATUS_PRESENT).count()
    absent = total - present
    overall_pct = round((present / total) * 100.0, 1) if total else 0.0

    # Subject-wise aggregation
    subject_stats = {}
    for rec in records:
        sub = rec.session.subject
        if sub.id not in subject_stats:
            subject_stats[sub.id] = {'subject': sub, 'total': 0, 'present': 0, 'absent': 0}
        subject_stats[sub.id]['total'] += 1
        if rec.status == AttendanceRecord.STATUS_PRESENT:
            subject_stats[sub.id]['present'] += 1
        else:
            subject_stats[sub.id]['absent'] += 1

    subject_rows = []
    for item in subject_stats.values():
        pct = round((item['present'] / item['total']) * 100.0, 1) if item['total'] else 0.0
        item['percentage'] = pct
        subject_rows.append(item)

    return render(request, 'attendance/student_attendance.html', {
        'overall_total': total,
        'overall_present': present,
        'overall_absent': absent,
        'overall_pct': overall_pct,
        'subject_rows': subject_rows,
        'records': records[:50],
    })


def _build_attendance_report_data(request):
    class_id = request.GET.get('class_id', '')
    subject_id = request.GET.get('subject_id', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')

    classes = AcademicClass.objects.all()
    selected_class = AcademicClass.objects.filter(id=class_id).first() if class_id else classes.first()

    sessions = AttendanceSession.objects.select_related('academic_class', 'subject', 'taken_by').all()
    if selected_class:
        sessions = sessions.filter(academic_class=selected_class)
    if subject_id:
        sessions = sessions.filter(subject_id=subject_id)
    if start_date:
        sessions = sessions.filter(date__gte=start_date)
    if end_date:
        sessions = sessions.filter(date__lte=end_date)

    sessions = sessions.order_by('date', 'time_slot')
    students = (
        CustomUser.objects.filter(role=CustomUser.ROLE_STUDENT, student_class=selected_class)
        .order_by('roll_number')
        if selected_class else CustomUser.objects.none()
    )

    records = AttendanceRecord.objects.filter(session__in=sessions)
    rec_map = {(r.session_id, r.student_id): r.status for r in records}

    student_summaries = []
    for stu in students:
        total_s = 0
        present_s = 0
        cells = []
        for sess in sessions:
            st = rec_map.get((sess.id, stu.id))
            if st is not None:
                total_s += 1
                if st == AttendanceRecord.STATUS_PRESENT:
                    present_s += 1
                cells.append('P' if st == AttendanceRecord.STATUS_PRESENT else 'A')
            else:
                cells.append('-')
        pct = round((present_s / total_s) * 100.0, 1) if total_s else 0.0
        student_summaries.append({
            'student': stu,
            'total': total_s,
            'present': present_s,
            'absent': total_s - present_s,
            'percentage': pct,
            'cells': cells,
        })

    return {
        'classes': classes,
        'subjects': Subject.objects.filter(academic_class=selected_class) if selected_class else Subject.objects.all(),
        'selected_class': selected_class,
        'selected_subject_id': subject_id,
        'start_date': start_date,
        'end_date': end_date,
        'sessions': sessions,
        'student_summaries': student_summaries,
    }


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def admin_attendance_report_view(request):
    context = _build_attendance_report_data(request)
    return render(request, 'attendance/admin_report.html', context)


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def export_attendance_excel(request):
    data = _build_attendance_report_data(request)
    wb = openpyxl.Workbook()
    ws = wb.active
    cls_name = data['selected_class'].name if data['selected_class'] else 'All'
    ws.title = f'Attendance {cls_name}'[:31]

    headers = ['Roll Number', 'Student Name', 'Class']
    for sess in data['sessions']:
        headers.append(f"{sess.date.strftime('%d-%b')} ({sess.subject.code})")
    headers.extend(['Present', 'Total Sessions', 'Attendance %'])
    ws.append(headers)

    header_fill = PatternFill(start_color='1E3A8A', end_color='1E3A8A', fill_type='solid')
    header_font = Font(color='FFFFFF', bold=True)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for row in data['student_summaries']:
        excel_row = [
            row['student'].roll_number,
            row['student'].full_name,
            cls_name,
            *row['cells'],
            row['present'],
            row['total'],
            f"{row['percentage']}%",
        ]
        ws.append(excel_row)

    for col in ws.columns:
        max_len = max(len(str(c.value or '')) for c in col)
        ws.column_dimensions[col[0].column_letter].width = max(12, min(28, max_len + 3))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="attendance_report_{cls_name}.xlsx"'
    return response


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def printable_attendance_register(request):
    data = _build_attendance_report_data(request)
    data['generated_at'] = timezone.now()
    return render(request, 'attendance/printable_register.html', data)


# ---------------------------------------------------------------------------
# 100% Free In-App Messaging & Notice Board
# ---------------------------------------------------------------------------

@login_required
def inbox_view(request):
    filter_type = request.GET.get('type', '')
    msgs = get_visible_messages_for_user(request.user).select_related('sender', 'recipient', 'target_class')
    if filter_type in (Message.TYPE_DIRECT, Message.TYPE_ANNOUNCEMENT):
        msgs = msgs.filter(message_type=filter_type)

    read_ids = set(
        MessageReadReceipt.objects.filter(user=request.user).values_list('message_id', flat=True)
    )

    return render(request, 'messaging/inbox.html', {
        'messages_list': msgs,
        'read_ids': read_ids,
        'filter_type': filter_type,
    })


@role_required(CustomUser.ROLE_ADMIN, CustomUser.ROLE_TEACHER)
def compose_message_view(request):
    form = MessageComposeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        mtype = form.cleaned_data['message_type']
        msg = Message.objects.create(
            sender=request.user,
            message_type=mtype,
            recipient=form.cleaned_data.get('recipient_user') if mtype == Message.TYPE_DIRECT else None,
            target_class=form.cleaned_data.get('target_class') if mtype == Message.TYPE_ANNOUNCEMENT else None,
            subject=form.cleaned_data['subject'],
            body=form.cleaned_data['body'],
        )
        # Sender automatically marks their own message as read
        MessageReadReceipt.objects.get_or_create(message=msg, user=request.user)
        messages.success(request, 'Message / Notice sent successfully!')
        return redirect('inbox')

    all_users = CustomUser.objects.filter(is_active=True).order_by('roll_number')
    return render(request, 'messaging/compose.html', {
        'form': form,
        'all_users': all_users,
    })


@login_required
def message_detail_view(request, message_id):
    visible = get_visible_messages_for_user(request.user)
    msg = get_object_or_404(visible.select_related('sender', 'recipient', 'target_class'), id=message_id)
    MessageReadReceipt.objects.get_or_create(message=msg, user=request.user)
    return render(request, 'messaging/message_detail.html', {'msg': msg})
