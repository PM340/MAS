from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    AcademicClass,
    Assignment,
    AttendanceRecord,
    AttendanceSession,
    CustomUser,
    Exam,
    ExamQuestion,
    Message,
    Question,
    Subject,
)


class Command(BaseCommand):
    help = 'Seed initial Acadex LMS classes, users, question bank, exam, assignment, attendance, and notices.'

    def handle(self, *args, **options):
        if CustomUser.objects.filter(roll_number='ADMIN001').exists():
            self.stdout.write(self.style.NOTICE('Acadex LMS already seeded. Skipping.'))
            return

        # 1. Classes
        bba, _ = AcademicClass.objects.get_or_create(
            code='BBA',
            defaults={'name': 'BBA', 'description': 'Bachelor of Business Administration'},
        )
        bcom, _ = AcademicClass.objects.get_or_create(
            code='BCOM',
            defaults={'name': 'BCom', 'description': 'Bachelor of Commerce'},
        )

        # 2. Users
        admin = CustomUser.objects.create_superuser(
            roll_number='ADMIN001',
            full_name='Dr. Vikram Rao (Super Admin)',
            password='Admin@2026',
        )

        teacher = CustomUser.objects.create_user(
            roll_number='TCH001',
            full_name='Prof. Meera Iyer',
            password='TCH001@2026',
            role=CustomUser.ROLE_TEACHER,
            must_change_password=True,
        )
        teacher.assigned_classes.add(bba, bcom)

        s1 = CustomUser.objects.create_user(
            roll_number='BBA001',
            full_name='Aarav Mehta',
            password='BBA001@2026',
            role=CustomUser.ROLE_STUDENT,
            student_class=bba,
            must_change_password=True,
        )
        s2 = CustomUser.objects.create_user(
            roll_number='BBA002',
            full_name='Diya Sharma',
            password='BBA002@2026',
            role=CustomUser.ROLE_STUDENT,
            student_class=bba,
            must_change_password=True,
        )
        s3 = CustomUser.objects.create_user(
            roll_number='BCOM001',
            full_name='Rohan Verma',
            password='BCOM001@2026',
            role=CustomUser.ROLE_STUDENT,
            student_class=bcom,
            must_change_password=True,
        )

        # 3. Subjects
        sub_bm, _ = Subject.objects.get_or_create(
            code='BBA101',
            academic_class=bba,
            defaults={'name': 'Business Mathematics', 'teacher': teacher},
        )
        sub_econ, _ = Subject.objects.get_or_create(
            code='BBA102',
            academic_class=bba,
            defaults={'name': 'Microeconomics', 'teacher': teacher},
        )
        sub_acct, _ = Subject.objects.get_or_create(
            code='BCOM101',
            academic_class=bcom,
            defaults={'name': 'Financial Accounting', 'teacher': teacher},
        )

        # 4. Question Bank (with MathJax LaTeX)
        q1 = Question.objects.create(
            subject=sub_bm,
            created_by=teacher,
            question_type=Question.TYPE_MCQ,
            prompt=r'Evaluate the definite integral: $$ \int_{0}^{2} 3x^2 \, dx $$',
            option_a='6',
            option_b='8',
            option_c='12',
            option_d='4',
            correct_option='B',
            marks=2,
            difficulty=Question.DIFF_EASY,
        )
        q2 = Question.objects.create(
            subject=sub_bm,
            created_by=teacher,
            question_type=Question.TYPE_MCQ,
            prompt=r'If the total cost function is \( C(x) = 5x^2 + 20x + 100 \), what is the marginal cost \( MC(x) \) at \( x = 4 \)?',
            option_a='40',
            option_b='50',
            option_c='60',
            option_d='80',
            correct_option='C',
            marks=2,
            difficulty=Question.DIFF_MEDIUM,
        )
        q3 = Question.objects.create(
            subject=sub_bm,
            created_by=teacher,
            question_type=Question.TYPE_FITB,
            prompt=r'The determinant of the identity matrix \( I_2 = \begin{pmatrix} 1 & 0 \\ 0 & 1 \end{pmatrix} \) is ________.',
            correct_answer_text='1, one',
            marks=2,
            difficulty=Question.DIFF_EASY,
        )
        q4 = Question.objects.create(
            subject=sub_bm,
            created_by=teacher,
            question_type=Question.TYPE_SHORT,
            prompt=r'Explain the break-even point in business mathematics and write the formula relating Revenue \( R(x) \) and Cost \( C(x) \).',
            model_answer=r'The break-even point occurs when Total Revenue equals Total Cost, i.e., \( R(x) = C(x) \), resulting in zero profit.',
            marks=4,
            difficulty=Question.DIFF_MEDIUM,
        )

        # 5. Moodle Exam
        exam = Exam.objects.create(
            title='Mid-Term Assessment: Business Mathematics',
            academic_class=bba,
            subject=sub_bm,
            created_by=teacher,
            duration_minutes=20,
            passing_percentage=40.0,
            max_tab_switches=3,
            is_published=True,
        )
        for idx, q in enumerate([q1, q2, q3, q4], start=1):
            ExamQuestion.objects.create(exam=exam, question=q, order=idx)

        # 6. Assignment
        Assignment.objects.create(
            title='Matrix Applications in Supply Chain Optimization',
            description='Submit a concise 2-page PDF or DOCX report demonstrating how Leontief input-output matrices model departmental resource allocation.',
            academic_class=bba,
            subject=sub_bm,
            created_by=teacher,
            due_date=timezone.now() + timedelta(days=7),
            max_marks=25,
        )
        Assignment.objects.create(
            title='Trial Balance & Ledger Reconciliation Sheet',
            description='Prepare a complete trial balance and income statement from the provided ledger transactions in PDF or image format.',
            academic_class=bcom,
            subject=sub_acct,
            created_by=teacher,
            due_date=timezone.now() + timedelta(days=5),
            max_marks=20,
        )

        # 7. Attendance Sessions
        today = timezone.localdate()
        sess1 = AttendanceSession.objects.create(
            academic_class=bba,
            subject=sub_bm,
            date=today - timedelta(days=1),
            time_slot='09:30 AM - 10:30 AM',
            taken_by=teacher,
        )
        AttendanceRecord.objects.create(session=sess1, student=s1, status=AttendanceRecord.STATUS_PRESENT)
        AttendanceRecord.objects.create(session=sess1, student=s2, status=AttendanceRecord.STATUS_ABSENT)

        sess2 = AttendanceSession.objects.create(
            academic_class=bba,
            subject=sub_econ,
            date=today,
            time_slot='10:45 AM - 11:45 AM',
            taken_by=teacher,
        )
        AttendanceRecord.objects.create(session=sess2, student=s1, status=AttendanceRecord.STATUS_PRESENT)
        AttendanceRecord.objects.create(session=sess2, student=s2, status=AttendanceRecord.STATUS_PRESENT)

        sess3 = AttendanceSession.objects.create(
            academic_class=bcom,
            subject=sub_acct,
            date=today,
            time_slot='11:45 AM - 12:45 PM',
            taken_by=teacher,
        )
        AttendanceRecord.objects.create(session=sess3, student=s3, status=AttendanceRecord.STATUS_PRESENT)

        # 8. Notice Board & Direct Messages
        Message.objects.create(
            sender=admin,
            message_type=Message.TYPE_ANNOUNCEMENT,
            target_class=None,
            subject='Welcome to Acadex Academic Term 2026',
            body='All students and faculty are advised to change their default onboarding password on first login and review their class schedule.',
        )
        Message.objects.create(
            sender=teacher,
            message_type=Message.TYPE_ANNOUNCEMENT,
            target_class=bba,
            subject='BBA Mid-Term Business Mathematics Exam Published',
            body='The timed Mid-Term Assessment for Business Mathematics is now live. Please ensure a stable browser session—tab switching is strictly monitored.',
        )
        Message.objects.create(
            sender=teacher,
            message_type=Message.TYPE_DIRECT,
            recipient=s1,
            subject='Lab Submission Reminder',
            body='Hi Aarav, please upload your Matrix Applications assignment before the Friday deadline.',
        )

        self.stdout.write(self.style.SUCCESS('Successfully seeded Acadex LMS demo dataset.'))
