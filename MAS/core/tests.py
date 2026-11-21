import io
import json
from datetime import timedelta

import openpyxl
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicClass,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    CustomUser,
    Exam,
    ExamAttempt,
    ExamQuestion,
    Message,
    Question,
    Subject,
)


class LmsEndToEndTests(TestCase):
    def setUp(self):
        self.bba = AcademicClass.objects.create(name='BBA', code='BBA')
        self.admin = CustomUser.objects.create_superuser('ADMIN001', 'Admin User', 'Admin@2026')
        self.teacher = CustomUser.objects.create_user(
            'TCH001', 'Prof Teacher', 'TCH001@2026',
            role=CustomUser.ROLE_TEACHER, must_change_password=False,
        )
        self.teacher.assigned_classes.add(self.bba)
        self.student = CustomUser.objects.create_user(
            'BBA001', 'Student One', 'BBA001@2026',
            role=CustomUser.ROLE_STUDENT, student_class=self.bba, must_change_password=True,
        )
        self.subject = Subject.objects.create(
            name='Business Math', code='BM101', academic_class=self.bba, teacher=self.teacher
        )
        self.client = Client()

    def test_forced_password_change_on_first_login(self):
        resp = self.client.post(reverse('login'), {'roll_number': 'BBA001', 'password': 'BBA001@2026'})
        self.assertRedirects(resp, reverse('force_password_change'))

        # Middleware blocks dashboard until password changed
        dash = self.client.get(reverse('dashboard'))
        self.assertRedirects(dash, reverse('force_password_change'))

        # Setting same default password fails validation
        bad = self.client.post(reverse('force_password_change'), {
            'new_password': 'BBA001@2026',
            'confirm_password': 'BBA001@2026',
        })
        self.assertEqual(bad.status_code, 200)

        # Setting new private password succeeds
        ok = self.client.post(reverse('force_password_change'), {
            'new_password': 'MySecretPassword99',
            'confirm_password': 'MySecretPassword99',
        })
        self.assertRedirects(ok, reverse('dashboard'))
        self.student.refresh_from_db()
        self.assertFalse(self.student.must_change_password)

    def test_exclusive_delete_rights_admin_only(self):
        self.client.force_login(self.teacher)
        resp = self.client.post(reverse('delete_user', args=[self.student.id]))
        self.assertRedirects(resp, reverse('dashboard'))
        self.assertTrue(CustomUser.objects.filter(id=self.student.id).exists())

        self.client.force_login(self.admin)
        resp2 = self.client.post(reverse('delete_user', args=[self.student.id]))
        self.assertRedirects(resp2, reverse('manage_users'))
        self.assertFalse(CustomUser.objects.filter(id=self.student.id).exists())

    def test_bulk_excel_onboarding_and_credentials_download(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Roll Number', 'Name', 'Class/Department', 'Role'])
        ws.append(['BCOM501', 'Kavya Shah', 'BCom', 'Student'])
        ws.append(['TCH501', 'Prof Vikram', 'BCom', 'Teacher'])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        self.client.force_login(self.admin)
        upload = SimpleUploadedFile(
            'roster.xlsx', buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp = self.client.post(reverse('bulk_onboarding'), {'excel_file': upload})
        self.assertEqual(resp.status_code, 200)

        self.assertTrue(AcademicClass.objects.filter(name='BCom').exists())
        u = CustomUser.objects.get(roll_number='BCOM501')
        self.assertTrue(u.check_password('BCOM501@2026'))
        self.assertTrue(u.must_change_password)

        cred_resp = self.client.get(reverse('download_credentials_excel'))
        self.assertEqual(cred_resp.status_code, 200)
        self.assertIn('spreadsheetml', cred_resp['Content-Type'])

        roster_resp = self.client.get(reverse('printable_roster'))
        self.assertEqual(roster_resp.status_code, 200)
        self.assertContains(roster_resp, 'BCOM501')

    def test_moodle_exam_engine_timer_tab_switch_and_autograde(self):
        q1 = Question.objects.create(
            subject=self.subject, created_by=self.teacher,
            question_type=Question.TYPE_MCQ, prompt='2 + 2 = ?',
            option_a='3', option_b='4', option_c='5', option_d='6',
            correct_option='B', marks=2,
        )
        q2 = Question.objects.create(
            subject=self.subject, created_by=self.teacher,
            question_type=Question.TYPE_FITB, prompt='Capital of India is ____',
            correct_answer_text='New Delhi, Delhi', marks=2,
        )
        exam = Exam.objects.create(
            title='Quiz 1', academic_class=self.bba, subject=self.subject,
            created_by=self.teacher, duration_minutes=15, max_tab_switches=2, is_published=True,
        )
        ExamQuestion.objects.create(exam=exam, question=q1, order=1)
        ExamQuestion.objects.create(exam=exam, question=q2, order=2)

        # Printable paper renders cleanly with answer key toggle
        self.client.force_login(self.teacher)
        paper = self.client.get(reverse('printable_paper', args=[exam.id]) + '?answer_key=1')
        self.assertEqual(paper.status_code, 200)
        self.assertContains(paper, '2 + 2 = ?')

        # Student takes exam
        self.student.must_change_password = False
        self.student.save()
        self.client.force_login(self.student)
        take_resp = self.client.get(reverse('take_exam', args=[exam.id]))
        self.assertEqual(take_resp.status_code, 200)
        self.assertNotContains(take_resp, 'sidebar-acadex py-3')
        self.assertContains(take_resp, 'submitExamModal')
        self.assertContains(take_resp, 'countdownTimerCard')
        self.assertContains(take_resp, 'timerSidebarDisplay')
        self.assertContains(take_resp, 'ExamCountdownTimer')

        attempt = ExamAttempt.objects.get(exam=exam, student=self.student)
        save_resp = self.client.post(
            reverse('save_exam_answer', args=[attempt.id]),
            data=json.dumps({'question_id': q1.id, 'selected_option': 'B', 'text_answer': ''}),
            content_type='application/json',
        )
        self.assertIn('remaining_seconds', save_resp.json())
        self.client.post(
            reverse('save_exam_answer', args=[attempt.id]),
            data=json.dumps({'question_id': q2.id, 'selected_option': '', 'text_answer': 'delhi'}),
            content_type='application/json',
        )

        # Tab switch 1 -> warning, not auto-submitted
        t1 = self.client.post(reverse('log_tab_switch', args=[attempt.id]), data='{}', content_type='application/json')
        self.assertFalse(t1.json()['auto_submitted'])

        # Tab switch 2 -> auto-submits and auto-grades!
        t2 = self.client.post(reverse('log_tab_switch', args=[attempt.id]), data='{}', content_type='application/json')
        self.assertTrue(t2.json()['auto_submitted'])

        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ExamAttempt.STATUS_AUTO_SUBMITTED)
        self.assertEqual(attempt.score, 4.0)
        self.assertTrue(attempt.is_fully_graded)

        # Timer reaching zero triggers auto_submitted=1 via submit endpoint
        exam2 = Exam.objects.create(
            title='Timed Quiz 2', academic_class=self.bba, subject=self.subject,
            created_by=self.teacher, duration_minutes=5, is_published=True,
        )
        ExamQuestion.objects.create(exam=exam2, question=q1, order=1)
        self.client.get(reverse('take_exam', args=[exam2.id]))
        attempt2 = ExamAttempt.objects.get(exam=exam2, student=self.student)
        sub_resp = self.client.post(reverse('submit_exam', args=[attempt2.id]), {
            'auto_submitted': '1',
            f'q_opt_{q1.id}': 'B',
        })
        self.assertRedirects(sub_resp, reverse('exam_result', args=[attempt2.id]))
        attempt2.refresh_from_db()
        self.assertEqual(attempt2.status, ExamAttempt.STATUS_AUTO_SUBMITTED)
        self.assertEqual(attempt2.score, 2.0)

    def test_assignments_attendance_and_messaging(self):
        self.student.must_change_password = False
        self.student.save()

        # Attendance default Present + report + Excel export
        self.client.force_login(self.teacher)
        att_resp = self.client.post(reverse('take_attendance'), {
            'save_attendance': '1',
            'academic_class': self.bba.id,
            'subject': self.subject.id,
            'date': timezone.localdate().isoformat(),
            'time_slot': '09:30 AM - 10:30 AM',
            f'status_{self.student.id}': 'PRESENT',
        })
        self.assertEqual(att_resp.status_code, 302)
        self.assertEqual(AttendanceRecord.objects.filter(student=self.student, status='PRESENT').count(), 1)

        excel_resp = self.client.get(reverse('export_attendance_excel'))
        self.assertEqual(excel_resp.status_code, 200)

        # Notice Board message + unread badge for student
        self.client.post(reverse('compose_message'), {
            'message_type': Message.TYPE_ANNOUNCEMENT,
            'target_class': self.bba.id,
            'subject': 'BBA Seminar',
            'body': 'Join at 2 PM.',
        })
        self.client.force_login(self.student)
        dash = self.client.get(reverse('dashboard'))
        self.assertContains(dash, '1 Unread')
        msg = Message.objects.get(subject='BBA Seminar')
        self.client.get(reverse('message_detail', args=[msg.id]))
        dash2 = self.client.get(reverse('dashboard'))
        self.assertNotContains(dash2, '1 Unread')

    def test_second_phone_login_auto_submits_and_closes_exam(self):
        self.student.must_change_password = False
        self.student.save()
        q1 = Question.objects.create(
            subject=self.subject, created_by=self.teacher,
            question_type=Question.TYPE_MCQ, prompt='1 + 1 = ?',
            option_a='2', option_b='3', correct_option='A', marks=5,
        )
        exam = Exam.objects.create(
            title='Single Device Security Exam', academic_class=self.bba, subject=self.subject,
            created_by=self.teacher, duration_minutes=20, is_published=True,
        )
        ExamQuestion.objects.create(exam=exam, question=q1, order=1)

        # Phone 1 logs in and starts the exam
        phone1 = Client()
        phone1.post(reverse('login'), {'roll_number': 'BBA001', 'password': 'BBA001@2026'})
        take_resp = phone1.get(reverse('take_exam', args=[exam.id]))
        self.assertEqual(take_resp.status_code, 200)
        attempt = ExamAttempt.objects.get(exam=exam, student=self.student)
        self.assertEqual(attempt.status, ExamAttempt.STATUS_IN_PROGRESS)

        # Save an answer from Phone 1
        phone1.post(
            reverse('save_exam_answer', args=[attempt.id]),
            data=json.dumps({'question_id': q1.id, 'selected_option': 'A', 'text_answer': ''}),
            content_type='application/json',
        )

        # Phone 2 logs in with the same student's credentials
        phone2 = Client()
        login2 = phone2.post(reverse('login'), {'roll_number': 'BBA001', 'password': 'BBA001@2026'})
        self.assertEqual(login2.status_code, 302)

        # Exam attempt must immediately be auto-submitted and score preserved
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, ExamAttempt.STATUS_AUTO_SUBMITTED)
        self.assertEqual(attempt.score, 5.0)
        self.assertIn('another phone', attempt.termination_reason.lower())

        # Phone 1 heartbeat immediately triggers termination/close payload
        hb = phone1.get(reverse('exam_heartbeat', args=[attempt.id]))
        self.assertEqual(hb.status_code, 200)
        hb_data = hb.json()
        self.assertTrue(hb_data['terminated'])
        self.assertEqual(hb_data['redirect_url'], reverse('exam_result', args=[attempt.id]))

    def test_pdf_question_import_with_math_symbols(self):
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        from .services import _register_pdf_fonts

        font_reg, _ = _register_pdf_fonts()
        pdf_buf = io.BytesIO()
        c = canvas.Canvas(pdf_buf, pagesize=A4)
        c.setFont(font_reg, 11)
        c.drawString(50, 780, '1. Evaluate ∫ x² dx and check √9 ≤ π. [2 Marks]')
        c.drawString(65, 760, '(A) x³/3 + C   (B) x² + C   (C) 2x   (D) None')
        c.drawString(65, 742, 'Answer: A')
        c.drawString(50, 695, '2. If α + β = 90°, find sin θ when θ = 30°. [3 Marks]')
        c.save()
        pdf_buf.seek(0)

        self.client.force_login(self.teacher)
        pdf_upload = SimpleUploadedFile('math_questions.pdf', pdf_buf.getvalue(), content_type='application/pdf')
        with self.settings():
            import os
            prev_key = os.environ.get('GEMINI_API_KEY')
            os.environ['GEMINI_API_KEY'] = 'dummy-key-must-not-be-called-for-text-pdf'
            try:
                resp = self.client.post(reverse('question_import_pdf'), {
                    'subject': self.subject.id,
                    'pdf_file': pdf_upload,
                    'extraction_mode': 'auto',
                })
            finally:
                if prev_key is None:
                    os.environ.pop('GEMINI_API_KEY', None)
                else:
                    os.environ['GEMINI_API_KEY'] = prev_key

        self.assertEqual(resp.status_code, 200)
        extracted = self.client.session.get('pdf_extracted_questions', [])
        self.assertEqual(len(extracted), 2)
        self.assertEqual(extracted[0]['question_type'], Question.TYPE_MCQ)
        self.assertEqual(extracted[0]['correct_option'], 'A')
        self.assertTrue(extracted[0]['option_c'])
        self.assertIn(r'\int', extracted[0]['prompt'])
        self.assertIn(r'\sqrt', extracted[0]['prompt'])

        save_resp = self.client.post(reverse('question_import_pdf'), {
            'save_selected': '1',
            'subject_id': self.subject.id,
            'row_count': '2',
            'include_0': '1',
            'question_type_0': extracted[0]['question_type'],
            'prompt_0': extracted[0]['prompt'],
            'option_a_0': extracted[0]['option_a'],
            'option_b_0': extracted[0]['option_b'],
            'correct_option_0': extracted[0]['correct_option'],
            'marks_0': '2',
            'difficulty_0': 'MEDIUM',
            'include_1': '1',
            'question_type_1': extracted[1]['question_type'],
            'prompt_1': extracted[1]['prompt'],
            'marks_1': '3',
            'difficulty_1': 'MEDIUM',
        })
        self.assertRedirects(save_resp, reverse('question_bank'))
        self.assertEqual(Question.objects.filter(subject=self.subject).count(), 2)

    def test_upload_paper_format_generates_word_and_pdf(self):
        import zipfile
        import docx

        q1 = Question.objects.create(
            subject=self.subject, created_by=self.teacher,
            question_type=Question.TYPE_MCQ, prompt=r'Find \( \sqrt{16} \)',
            option_a='2', option_b='4', correct_option='B', marks=2,
        )
        q2 = Question.objects.create(
            subject=self.subject, created_by=self.teacher,
            question_type=Question.TYPE_SHORT, prompt=r'Define \( \pi \) and state its approximate value.',
            model_answer='3.14159', marks=5,
        )
        exam = Exam.objects.create(
            title='Midterm Calculus Paper', academic_class=self.bba, subject=self.subject,
            created_by=self.teacher, duration_minutes=60, is_published=True,
        )
        ExamQuestion.objects.create(exam=exam, question=q1, order=1)
        ExamQuestion.objects.create(exam=exam, question=q2, order=2)

        # Build custom .docx paper format template
        fmt_doc = docx.Document()
        fmt_doc.add_paragraph('NATIONAL COMMERCE ACADEMY')
        fmt_doc.add_paragraph('{{TITLE}} | {{CLASS}} | {{MARKS}} Marks')
        fmt_doc.add_paragraph('General Instructions:')
        fmt_doc.add_paragraph('1. Attempt all questions neatly.')
        fmt_doc.add_paragraph('SECTION A — OBJECTIVE MCQ')
        fmt_doc.add_paragraph('SECTION B — SHORT ANSWER')
        fmt_buf = io.BytesIO()
        fmt_doc.save(fmt_buf)
        fmt_buf.seek(0)

        self.client.force_login(self.teacher)
        fmt_file = SimpleUploadedFile(
            'custom_format.docx',
            fmt_buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        upload_resp = self.client.post(reverse('format_paper_generator_for_exam', args=[exam.id]), {
            'exam': exam.id,
            'format_file': fmt_file,
            'include_answer_key': 'on',
        })
        self.assertEqual(upload_resp.status_code, 200)
        self.assertContains(upload_resp, 'NATIONAL COMMERCE ACADEMY')

        # Download Word (.docx)
        docx_resp = self.client.get(reverse('download_formatted_paper', args=[exam.id, 'docx']))
        self.assertEqual(docx_resp.status_code, 200)
        self.assertIn('wordprocessingml', docx_resp['Content-Type'])
        parsed_docx = docx.Document(io.BytesIO(docx_resp.content))
        docx_text = '\n'.join(p.text for p in parsed_docx.paragraphs)
        self.assertIn('Midterm Calculus Paper', docx_text)
        self.assertIn('SECTION A', docx_text)

        # Download PDF (.pdf)
        pdf_resp = self.client.get(reverse('download_formatted_paper', args=[exam.id, 'pdf']))
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp['Content-Type'], 'application/pdf')
        self.assertTrue(pdf_resp.content.startswith(b'%PDF'))

        # Download Both (.zip)
        zip_resp = self.client.get(reverse('download_formatted_paper', args=[exam.id, 'both']))
        self.assertEqual(zip_resp.status_code, 200)
        self.assertEqual(zip_resp['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            names = zf.namelist()
            self.assertTrue(any(n.endswith('.docx') for n in names))
            self.assertTrue(any(n.endswith('.pdf') for n in names))

    def test_large_pdf_question_save_over_1000_fields(self):
        self.client.force_login(self.teacher)
        # 1. JSON payload route with 150 questions
        payload = [
            {
                'question_type': 'MCQ',
                'prompt': f'Sample math Q{i}: \\( x^{i} \\)',
                'option_a': '1',
                'option_b': '2',
                'correct_option': 'A',
                'marks': 1,
                'difficulty': 'MEDIUM',
            }
            for i in range(150)
        ]
        resp = self.client.post(reverse('question_import_pdf'), {
            'save_selected': '1',
            'subject_id': self.subject.id,
            'questions_payload': json.dumps(payload),
        })
        self.assertRedirects(resp, reverse('question_bank'))
        self.assertEqual(Question.objects.filter(subject=self.subject).count(), 150)

        # 2. Fallback individual-fields route with 120 questions (1440+ POST fields > default 1000 limit)
        raw_post = {
            'save_selected': '1',
            'subject_id': str(self.subject.id),
            'row_count': '120',
        }
        for i in range(120):
            raw_post[f'include_{i}'] = '1'
            raw_post[f'question_type_{i}'] = 'SHORT'
            raw_post[f'prompt_{i}'] = f'Fallback Q{i}'
            raw_post[f'marks_{i}'] = '2'
            raw_post[f'difficulty_{i}'] = 'EASY'
            raw_post[f'option_a_{i}'] = ''
            raw_post[f'option_b_{i}'] = ''
            raw_post[f'option_c_{i}'] = ''
            raw_post[f'option_d_{i}'] = ''
            raw_post[f'correct_option_{i}'] = ''
            raw_post[f'correct_answer_text_{i}'] = ''
            raw_post[f'model_answer_{i}'] = f'Ans {i}'

        resp2 = self.client.post(reverse('question_import_pdf'), raw_post)
        self.assertRedirects(resp2, reverse('question_bank'))
        self.assertEqual(Question.objects.filter(subject=self.subject).count(), 270)

    def test_manual_add_student_and_teacher_and_validation(self):
        bcom = AcademicClass.objects.create(name='BCom', code='BCOM')
        self.client.force_login(self.admin)

        # Create Student manually with Semester
        stu_resp = self.client.post(reverse('manage_users'), {
            'roll_number': 'bba205',
            'full_name': 'Neha Kapoor',
            'role': CustomUser.ROLE_STUDENT,
            'student_class': self.bba.id,
            'semester': 'Sem 2',
        })
        self.assertRedirects(stu_resp, reverse('manage_users'))
        stu = CustomUser.objects.get(roll_number='BBA205')
        self.assertEqual(stu.role, CustomUser.ROLE_STUDENT)
        self.assertEqual(stu.student_class, self.bba)
        self.assertEqual(stu.semester, 'Sem 2')
        self.assertTrue(stu.check_password('BBA205@2026'))

        # Create Teacher manually assigned to BOTH BBA and BCom
        tch_resp = self.client.post(reverse('manage_users'), {
            'roll_number': 'tch205',
            'full_name': 'Prof. Rajesh Iyer',
            'role': CustomUser.ROLE_TEACHER,
            'assigned_classes': [self.bba.id, bcom.id],
        })
        self.assertRedirects(tch_resp, reverse('manage_users'))
        tch = CustomUser.objects.get(roll_number='TCH205')
        self.assertEqual(tch.role, CustomUser.ROLE_TEACHER)
        self.assertEqual(set(tch.assigned_classes.values_list('name', flat=True)), {'BBA', 'BCom'})

        # Duplicate roll number (case-insensitive) shows validation error and keeps modal open cleanly
        dup_resp = self.client.post(reverse('manage_users'), {
            'roll_number': 'bba205',
            'full_name': 'Duplicate User',
            'role': CustomUser.ROLE_STUDENT,
            'student_class': self.bba.id,
            'semester': 'Sem 1',
        })
        self.assertEqual(dup_resp.status_code, 200)
        self.assertTrue(dup_resp.context['show_create_modal'])
        self.assertContains(dup_resp, 'already exists')

    def test_multiclass_teacher_and_semester_student_excel_onboarding(self):
        self.client.force_login(self.admin)

        # Sample Excel template downloads cleanly with 5 columns
        sample_resp = self.client.get(reverse('download_sample_excel'))
        self.assertEqual(sample_resp.status_code, 200)
        wb_sample = openpyxl.load_workbook(io.BytesIO(sample_resp.content))
        ws_sample = wb_sample.active
        headers = [cell.value for cell in ws_sample[1]]
        self.assertEqual(headers, ['Roll Number', 'Name', 'Class/Department', 'Role', 'Semester'])

        # Upload Excel with multi-class teachers & semester-wise students
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Roll Number', 'Name', 'Class/Department', 'Role', 'Semester'])
        ws.append(['BBA111', 'Stu Sem1', 'BBA', 'Student', 'Sem 1'])
        ws.append(['BBA222', 'Stu Sem3', 'BBA Sem 3', 'Student', ''])
        ws.append(['BCOM444', 'Stu Sem4', 'BCom', 'Student', '4'])
        ws.append(['TCH888', 'Prof Multi', 'BBA, BCom', 'Teacher', ''])
        ws.append(['TCH999', 'Prof Dual', 'BBA & BCom & MBA', 'Teacher', ''])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        upload = SimpleUploadedFile(
            'multi_sem_roster.xlsx', buf.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp = self.client.post(reverse('bulk_onboarding'), {'excel_file': upload})
        self.assertEqual(resp.status_code, 200)

        s1 = CustomUser.objects.get(roll_number='BBA111')
        s2 = CustomUser.objects.get(roll_number='BBA222')
        s3 = CustomUser.objects.get(roll_number='BCOM444')
        self.assertEqual(s1.semester, 'Sem 1')
        self.assertEqual(s2.student_class.name, 'BBA')
        self.assertEqual(s2.semester, 'Sem 3')
        self.assertEqual(s3.student_class.name, 'BCom')
        self.assertEqual(s3.semester, 'Sem 4')

        t1 = CustomUser.objects.get(roll_number='TCH888')
        t2 = CustomUser.objects.get(roll_number='TCH999')
        self.assertEqual(set(t1.assigned_classes.values_list('name', flat=True)), {'BBA', 'BCom'})
        self.assertEqual(set(t2.assigned_classes.values_list('name', flat=True)), {'BBA', 'BCom', 'MBA'})

        # Filter manage_users and printable_roster by semester
        dir_resp = self.client.get(reverse('manage_users') + '?semester=Sem+3')
        self.assertContains(dir_resp, 'BBA222')
        self.assertNotContains(dir_resp, 'BBA111')

        roster_resp = self.client.get(reverse('printable_roster') + '?semester=Sem+4')
        self.assertContains(roster_resp, 'BCOM444')
        self.assertNotContains(roster_resp, 'BBA111')

        # Upload teacher-only file using default_role override and default_semester for students
        wb2 = openpyxl.Workbook()
        ws2 = wb2.active
        ws2.append(['Roll Number', 'Name', 'Class/Department'])
        ws2.append(['MBA101', 'Stu Override', 'MBA'])
        buf2 = io.BytesIO()
        wb2.save(buf2)
        buf2.seek(0)
        upload2 = SimpleUploadedFile(
            'sem2_students.xlsx', buf2.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp2 = self.client.post(reverse('bulk_onboarding'), {
            'excel_file': upload2,
            'default_role': CustomUser.ROLE_STUDENT,
            'default_semester': 'Sem 2',
        })
        self.assertEqual(resp2.status_code, 200)
        mba_stu = CustomUser.objects.get(roll_number='MBA101')
        self.assertEqual(mba_stu.role, CustomUser.ROLE_STUDENT)
        self.assertEqual(mba_stu.semester, 'Sem 2')

    def test_admin_can_edit_student_and_teacher_profiles(self):
        bcom = AcademicClass.objects.create(name='BCom', code='BCOM')

        # Non-admin (teacher) cannot access edit_user
        self.client.force_login(self.teacher)
        denied = self.client.get(reverse('edit_user', args=[self.student.id]))
        self.assertRedirects(denied, reverse('dashboard'))

        # Admin edits Student profile (change name, class, semester, active, must_change_password)
        self.client.force_login(self.admin)
        get_stu = self.client.get(reverse('edit_user', args=[self.student.id]))
        self.assertEqual(get_stu.status_code, 200)

        edit_stu = self.client.post(reverse('edit_user', args=[self.student.id]), {
            'roll_number': 'BBA001',
            'full_name': 'Student One Updated',
            'role': CustomUser.ROLE_STUDENT,
            'student_class': bcom.id,
            'semester': 'Sem 4',
            'bio': 'Honors track',
            'is_active': 'on',
        })
        self.assertRedirects(edit_stu, reverse('manage_users'))
        self.student.refresh_from_db()
        self.assertEqual(self.student.full_name, 'Student One Updated')
        self.assertEqual(self.student.student_class, bcom)
        self.assertEqual(self.student.semester, 'Sem 4')
        self.assertEqual(self.student.bio, 'Honors track')
        self.assertFalse(self.student.must_change_password)

        # Admin edits Teacher profile (assign both BBA & BCom, update bio & password)
        edit_tch = self.client.post(reverse('edit_user', args=[self.teacher.id]), {
            'roll_number': 'TCH001',
            'full_name': 'Prof Senior Updated',
            'role': CustomUser.ROLE_TEACHER,
            'assigned_classes': [self.bba.id, bcom.id],
            'bio': 'Department Head',
            'is_active': 'on',
            'new_password': 'TeacherNewPass99',
            'confirm_password': 'TeacherNewPass99',
        })
        self.assertRedirects(edit_tch, reverse('manage_users'))
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.full_name, 'Prof Senior Updated')
        self.assertEqual(set(self.teacher.assigned_classes.values_list('name', flat=True)), {'BBA', 'BCom'})
        self.assertEqual(self.teacher.bio, 'Department Head')
        self.assertTrue(self.teacher.check_password('TeacherNewPass99'))



