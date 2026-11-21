from datetime import timedelta
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone


class AcademicClass(models.Model):
    """Represents a department / cohort such as BBA, BCom, BCA, MBA."""
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Academic Class'
        verbose_name_plural = 'Academic Classes'

    def __str__(self):
        return f"{self.name} ({self.code})"


class CustomUserManager(BaseUserManager):
    """Manager for CustomUser using roll_number as the unique login identifier."""

    def create_user(self, roll_number, full_name, password=None, **extra_fields):
        if not roll_number:
            raise ValueError('Roll Number / System ID is required.')
        roll_number = str(roll_number).strip().upper()
        if not full_name:
            full_name = roll_number
        user = self.model(
            roll_number=roll_number,
            full_name=full_name.strip(),
            **extra_fields,
        )
        if password is None:
            password = f"{roll_number}@2026"
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, roll_number, full_name='Super Admin', password=None, **extra_fields):
        extra_fields.setdefault('role', CustomUser.ROLE_ADMIN)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('must_change_password', False)
        return self.create_user(roll_number, full_name, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """Custom LMS user authenticated via Roll Number / System ID (no email/phone required)."""

    ROLE_ADMIN = 'ADMIN'
    ROLE_TEACHER = 'TEACHER'
    ROLE_STUDENT = 'STUDENT'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Super Admin'),
        (ROLE_TEACHER, 'Teacher'),
        (ROLE_STUDENT, 'Student'),
    ]

    SEMESTER_CHOICES = [
        ('', 'Not Applicable / None'),
        ('Sem 1', 'Sem 1'),
        ('Sem 2', 'Sem 2'),
        ('Sem 3', 'Sem 3'),
        ('Sem 4', 'Sem 4'),
        ('Sem 5', 'Sem 5'),
        ('Sem 6', 'Sem 6'),
        ('Sem 7', 'Sem 7'),
        ('Sem 8', 'Sem 8'),
    ]

    roll_number = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Roll Number / System ID',
    )
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    student_class = models.ForeignKey(
        AcademicClass,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        verbose_name='Class / Department',
    )
    semester = models.CharField(
        max_length=20,
        choices=SEMESTER_CHOICES,
        blank=True,
        default='',
        verbose_name='Semester (Students)',
    )
    assigned_classes = models.ManyToManyField(
        AcademicClass,
        blank=True,
        related_name='teachers',
        verbose_name='Assigned Classes (Teachers)',
    )
    must_change_password = models.BooleanField(default=True)
    bio = models.TextField(blank=True)
    active_session_key = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = 'roll_number'
    REQUIRED_FIELDS = ['full_name']

    class Meta:
        ordering = ['role', 'roll_number']
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.roll_number} - {self.full_name} ({self.get_role_display()})"

    @property
    def is_super_admin(self):
        return self.role == self.ROLE_ADMIN or self.is_superuser

    @property
    def is_teacher(self):
        return self.role == self.ROLE_TEACHER

    @property
    def is_student(self):
        return self.role == self.ROLE_STUDENT

    @property
    def initials(self):
        parts = self.full_name.strip().split()
        if len(parts) >= 2:
            return f"{parts[0][0]}{parts[-1][0]}".upper()
        return self.full_name[:2].upper() if self.full_name else self.roll_number[:2].upper()

    @property
    def default_password(self):
        return f"{self.roll_number}@2026"


class Subject(models.Model):
    """Course subject belonging to an AcademicClass."""
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=30)
    academic_class = models.ForeignKey(
        AcademicClass,
        on_delete=models.CASCADE,
        related_name='subjects',
    )
    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={'role__in': ['TEACHER', 'ADMIN']},
        related_name='teaching_subjects',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('code', 'academic_class')
        ordering = ['academic_class__name', 'code']

    def __str__(self):
        return f"{self.code} - {self.name} ({self.academic_class.name})"


class Question(models.Model):
    """Question Bank supporting MCQs, Fill in the Blanks, and Short Answers with MathJax."""

    TYPE_MCQ = 'MCQ'
    TYPE_FITB = 'FITB'
    TYPE_SHORT = 'SHORT'
    TYPE_CHOICES = [
        (TYPE_MCQ, 'Multiple Choice (MCQ)'),
        (TYPE_FITB, 'Fill in the Blanks'),
        (TYPE_SHORT, 'Short Answer'),
    ]

    DIFF_EASY = 'EASY'
    DIFF_MEDIUM = 'MEDIUM'
    DIFF_HARD = 'HARD'
    DIFFICULTY_CHOICES = [
        (DIFF_EASY, 'Easy'),
        (DIFF_MEDIUM, 'Medium'),
        (DIFF_HARD, 'Hard'),
    ]

    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='questions')
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_questions',
    )
    question_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_MCQ)
    prompt = models.TextField(help_text='Supports LaTeX MathJax equations like \\( x^2 + y^2 \\) or $$ \\frac{a}{b} $$.')

    # MCQ options
    option_a = models.CharField(max_length=500, blank=True)
    option_b = models.CharField(max_length=500, blank=True)
    option_c = models.CharField(max_length=500, blank=True)
    option_d = models.CharField(max_length=500, blank=True)
    correct_option = models.CharField(
        max_length=1,
        choices=[('A', 'Option A'), ('B', 'Option B'), ('C', 'Option C'), ('D', 'Option D')],
        blank=True,
    )

    # FITB & Short Answer
    correct_answer_text = models.TextField(
        blank=True,
        help_text='For Fill in the Blanks, separate multiple acceptable answers with commas.',
    )
    model_answer = models.TextField(
        blank=True,
        help_text='Reference solution for Short Answer questions and Answer Key.',
    )

    marks = models.PositiveIntegerField(default=1)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default=DIFF_MEDIUM)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_question_type_display()}] {self.prompt[:60]}"

    def get_correct_display(self):
        if self.question_type == self.TYPE_MCQ:
            mapping = {
                'A': self.option_a,
                'B': self.option_b,
                'C': self.option_c,
                'D': self.option_d,
            }
            opt_text = mapping.get(self.correct_option, '')
            return f"Option {self.correct_option}: {opt_text}" if self.correct_option else 'Not set'
        if self.question_type == self.TYPE_FITB:
            return self.correct_answer_text or 'Not set'
        return self.model_answer or 'Manual grading'


class Exam(models.Model):
    """Timed Moodle-style Examination composed of questions from the Question Bank."""
    title = models.CharField(max_length=200)
    academic_class = models.ForeignKey(AcademicClass, on_delete=models.CASCADE, related_name='exams')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='exams')
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_exams',
    )
    duration_minutes = models.PositiveIntegerField(default=30)
    passing_percentage = models.FloatField(default=40.0)
    max_tab_switches = models.PositiveIntegerField(default=3)
    instructions = models.TextField(
        blank=True,
        default='Read all questions carefully. Switching browser tabs or leaving full focus is monitored and logged.',
    )
    questions = models.ManyToManyField(Question, through='ExamQuestion', related_name='exams')
    is_published = models.BooleanField(default=True)
    shuffle_questions = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.title} ({self.academic_class.name} - {self.subject.code})"

    @property
    def total_marks(self):
        return sum(eq.question.marks for eq in self.exam_questions.select_related('question').all())

    @property
    def question_count(self):
        return self.exam_questions.count()

    @property
    def passing_marks(self):
        return round((self.total_marks * self.passing_percentage) / 100.0, 2)


class ExamQuestion(models.Model):
    """Ordered link between Exam and Question."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='exam_questions')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='exam_links')
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['order', 'id']
        unique_together = ('exam', 'question')

    def __str__(self):
        return f"{self.exam.title} - Q{self.order}"


class ExamAttempt(models.Model):
    """Tracks a student's live timed attempt, tab-switch logs, and auto/manual grading."""

    STATUS_IN_PROGRESS = 'IN_PROGRESS'
    STATUS_SUBMITTED = 'SUBMITTED'
    STATUS_AUTO_SUBMITTED = 'AUTO_SUBMITTED'
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, 'In Progress'),
        (STATUS_SUBMITTED, 'Submitted'),
        (STATUS_AUTO_SUBMITTED, 'Auto-Submitted'),
    ]

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='attempts')
    student = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='exam_attempts')
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_IN_PROGRESS)
    tab_switch_count = models.PositiveIntegerField(default=0)
    tab_switch_log = models.JSONField(default=list, blank=True)
    question_order = models.JSONField(default=list, blank=True)
    active_session_key = models.CharField(max_length=64, blank=True)
    termination_reason = models.CharField(max_length=255, blank=True)
    score = models.FloatField(default=0.0)
    is_fully_graded = models.BooleanField(default=False)

    class Meta:
        unique_together = ('exam', 'student')
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.student.roll_number} - {self.exam.title} ({self.status})"

    def remaining_seconds(self):
        if self.status != self.STATUS_IN_PROGRESS:
            return 0
        end_time = self.started_at + timedelta(minutes=self.exam.duration_minutes)
        remaining = int((end_time - timezone.now()).total_seconds())
        return max(0, remaining)

    @property
    def percentage(self):
        total = self.exam.total_marks
        if not total:
            return 0.0
        return round((self.score / total) * 100.0, 2)

    @property
    def passed(self):
        return self.score >= self.exam.passing_marks

    def evaluate_attempt(self, auto_submitted=False):
        """Auto-grade MCQ & FITB answers; flag SHORT answers for manual grading."""
        total_score = 0.0
        has_ungraded_short = False

        answers_by_qid = {
            ans.question_id: ans
            for ans in self.answers.select_related('question').all()
        }

        for eq in self.exam.exam_questions.select_related('question').all():
            q = eq.question
            ans = answers_by_qid.get(q.id)
            if ans is None:
                ans = ExamAnswer.objects.create(attempt=self, question=q)

            if q.question_type == Question.TYPE_MCQ:
                if ans.selected_option and ans.selected_option == q.correct_option:
                    ans.is_correct = True
                    ans.marks_awarded = float(q.marks)
                else:
                    ans.is_correct = False
                    ans.marks_awarded = 0.0
                ans.save()
                total_score += ans.marks_awarded

            elif q.question_type == Question.TYPE_FITB:
                student_txt = (ans.text_answer or '').strip().lower()
                accepted = [
                    item.strip().lower()
                    for item in (q.correct_answer_text or '').split(',')
                    if item.strip()
                ]
                if student_txt and student_txt in accepted:
                    ans.is_correct = True
                    ans.marks_awarded = float(q.marks)
                else:
                    ans.is_correct = False
                    ans.marks_awarded = 0.0
                ans.save()
                total_score += ans.marks_awarded

            elif q.question_type == Question.TYPE_SHORT:
                if ans.marks_awarded is not None:
                    total_score += float(ans.marks_awarded)
                else:
                    has_ungraded_short = True

        self.score = round(total_score, 2)
        self.is_fully_graded = not has_ungraded_short
        if self.status == self.STATUS_IN_PROGRESS:
            self.status = self.STATUS_AUTO_SUBMITTED if auto_submitted else self.STATUS_SUBMITTED
            self.submitted_at = timezone.now()
        self.save()


class ExamAnswer(models.Model):
    """Stores a student's response and grading info for a single exam question."""
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.CharField(max_length=1, blank=True)
    text_answer = models.TextField(blank=True)
    is_marked_for_review = models.BooleanField(default=False)
    is_visited = models.BooleanField(default=False)
    marks_awarded = models.FloatField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    grader_feedback = models.TextField(blank=True)

    class Meta:
        unique_together = ('attempt', 'question')

    def __str__(self):
        return f"Attempt {self.attempt_id} - Q{self.question_id}"


class Assignment(models.Model):
    """Class-level coursework assignment created by Teachers or Admin."""
    title = models.CharField(max_length=200)
    description = models.TextField()
    academic_class = models.ForeignKey(AcademicClass, on_delete=models.CASCADE, related_name='assignments')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='assignments')
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_assignments',
    )
    due_date = models.DateTimeField()
    max_marks = models.PositiveIntegerField(default=100)
    attachment = models.FileField(
        upload_to='assignments/prompts/',
        blank=True,
        null=True,
        validators=[FileExtensionValidator(['pdf', 'docx', 'doc', 'png', 'jpg', 'jpeg', 'txt'])],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-due_date']

    def __str__(self):
        return f"{self.title} ({self.academic_class.name})"

    @property
    def is_overdue(self):
        return timezone.now() > self.due_date


class AssignmentSubmission(models.Model):
    """Student submission file (.pdf, .docx, images) with teacher grade & feedback."""
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='assignment_submissions')
    file = models.FileField(
        upload_to='assignments/submissions/',
        validators=[FileExtensionValidator(['pdf', 'docx', 'doc', 'png', 'jpg', 'jpeg', 'webp'])],
    )
    notes = models.TextField(blank=True)
    submitted_at = models.DateTimeField(auto_now=True)
    marks_obtained = models.FloatField(null=True, blank=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='graded_submissions',
    )
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('assignment', 'student')
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.student.roll_number} -> {self.assignment.title}"

    @property
    def is_graded(self):
        return self.marks_obtained is not None


class AttendanceSession(models.Model):
    """Represents a class lecture slot where attendance is recorded."""
    TIME_SLOT_CHOICES = [
        ('08:30 AM - 09:30 AM', '08:30 AM - 09:30 AM'),
        ('09:30 AM - 10:30 AM', '09:30 AM - 10:30 AM'),
        ('10:45 AM - 11:45 AM', '10:45 AM - 11:45 AM'),
        ('11:45 AM - 12:45 PM', '11:45 AM - 12:45 PM'),
        ('01:30 PM - 02:30 PM', '01:30 PM - 02:30 PM'),
        ('02:30 PM - 03:30 PM', '02:30 PM - 03:30 PM'),
        ('03:30 PM - 04:30 PM', '03:30 PM - 04:30 PM'),
    ]

    academic_class = models.ForeignKey(AcademicClass, on_delete=models.CASCADE, related_name='attendance_sessions')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='attendance_sessions')
    date = models.DateField(default=timezone.localdate)
    time_slot = models.CharField(max_length=60)
    taken_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='attendance_taken',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('academic_class', 'subject', 'date', 'time_slot')
        ordering = ['-date', 'time_slot']

    def __str__(self):
        return f"{self.academic_class.name} | {self.subject.code} | {self.date} ({self.time_slot})"


class AttendanceRecord(models.Model):
    """Individual student attendance status (Present / Absent) in a session."""
    STATUS_PRESENT = 'PRESENT'
    STATUS_ABSENT = 'ABSENT'
    STATUS_CHOICES = [
        (STATUS_PRESENT, 'Present'),
        (STATUS_ABSENT, 'Absent'),
    ]

    session = models.ForeignKey(AttendanceSession, on_delete=models.CASCADE, related_name='records')
    student = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='attendance_records')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PRESENT)
    remarks = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ('session', 'student')
        ordering = ['student__roll_number']

    def __str__(self):
        return f"{self.student.roll_number}: {self.status}"


class Message(models.Model):
    """100% Free In-App Direct Messaging & Class-Wide Notice Board."""
    TYPE_DIRECT = 'DIRECT'
    TYPE_ANNOUNCEMENT = 'ANNOUNCEMENT'
    TYPE_CHOICES = [
        (TYPE_DIRECT, 'Direct Message'),
        (TYPE_ANNOUNCEMENT, 'Class Notice / Announcement'),
    ]

    sender = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='sent_messages')
    message_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_DIRECT)
    recipient = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='direct_messages',
    )
    target_class = models.ForeignKey(
        AcademicClass,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='class_notices',
        help_text='Leave blank when sending a global notice to all classes.',
    )
    subject = models.CharField(max_length=200)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.get_message_type_display()}] {self.subject}"


class MessageReadReceipt(models.Model):
    """Tracks read status per user for both direct messages and class announcements."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='read_receipts')
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='read_messages')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user')

    def __str__(self):
        return f"{self.user.roll_number} read #{self.message_id}"
