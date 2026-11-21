from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

from .models import (
    AcademicClass,
    Assignment,
    AssignmentSubmission,
    CustomUser,
    Exam,
    Message,
    Question,
    Subject,
)


class LoginForm(forms.Form):
    roll_number = forms.CharField(
        max_length=50,
        label='Roll Number / System ID',
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'e.g. BBA001, TCH001, ADMIN001',
            'autocomplete': 'username',
            'autofocus': True,
        }),
    )
    password = forms.CharField(
        label='Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password',
        }),
    )


class ForcePasswordChangeForm(forms.Form):
    new_password = forms.CharField(
        min_length=6,
        label='New Private Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Minimum 6 characters',
        }),
    )
    confirm_password = forms.CharField(
        min_length=6,
        label='Confirm New Private Password',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Re-enter new password',
        }),
    )

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        pwd1 = cleaned.get('new_password')
        pwd2 = cleaned.get('confirm_password')
        if pwd1 and pwd2 and pwd1 != pwd2:
            raise ValidationError('Both passwords must match.')
        if pwd1 and pwd1 == self.user.default_password:
            raise ValidationError('Please choose a private password different from your default onboarding password.')
        return cleaned


class ProfileUpdateForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        min_length=6,
        label='New Password (optional)',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep current password'}),
    )
    confirm_password = forms.CharField(
        required=False,
        min_length=6,
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Confirm new password'}),
    )

    class Meta:
        model = CustomUser
        fields = ['full_name', 'bio']
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Short academic bio or interests...'}),
        }

    def clean(self):
        cleaned = super().clean()
        pwd1 = cleaned.get('new_password')
        pwd2 = cleaned.get('confirm_password')
        if pwd1 or pwd2:
            if pwd1 != pwd2:
                raise ValidationError('New passwords do not match.')
        return cleaned


class BulkOnboardingForm(forms.Form):
    excel_file = forms.FileField(
        label='Select Excel File (.xlsx)',
        validators=[FileExtensionValidator(['xlsx'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.xlsx',
        }),
    )
    default_role = forms.ChoiceField(
        required=False,
        choices=[
            ('', 'Use Role column in Excel (Recommended)'),
            (CustomUser.ROLE_STUDENT, 'Force all rows as Student'),
            (CustomUser.ROLE_TEACHER, 'Force all rows as Teacher (Multi-Class supported)'),
        ],
        label='Default Role Override (optional)',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    default_semester = forms.ChoiceField(
        required=False,
        choices=[('', 'Auto-detect from Excel Semester column / None')] + CustomUser.SEMESTER_CHOICES[1:],
        label='Default Semester for Students (optional)',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class AcademicClassForm(forms.ModelForm):
    class Meta:
        model = AcademicClass
        fields = ['name', 'code', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BBA, BCom'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BBA, BCOM'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Program description...'}),
        }


class SubjectForm(forms.ModelForm):
    class Meta:
        model = Subject
        fields = ['name', 'code', 'academic_class', 'teacher']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Financial Accounting'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BCOM101'}),
            'academic_class': forms.Select(attrs={'class': 'form-select'}),
            'teacher': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['teacher'].queryset = CustomUser.objects.filter(
            role__in=[CustomUser.ROLE_TEACHER, CustomUser.ROLE_ADMIN],
            is_active=True,
        )


class UserCreateForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ['roll_number', 'full_name', 'role', 'student_class', 'semester', 'assigned_classes']
        widgets = {
            'roll_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. BBA105 or TCH012'}),
            'full_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Full Name'}),
            'role': forms.Select(attrs={'class': 'form-select', 'id': 'id_create_role'}),
            'student_class': forms.Select(attrs={'class': 'form-select'}),
            'semester': forms.Select(attrs={'class': 'form-select'}),
            'assigned_classes': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
        }

    def clean_roll_number(self):
        roll = (self.cleaned_data.get('roll_number') or '').strip().upper()
        if not roll:
            raise ValidationError('Roll Number / System ID is required.')
        if CustomUser.objects.filter(roll_number=roll).exists():
            raise ValidationError(f'A user with Roll Number "{roll}" already exists.')
        return roll


class AdminUserEditForm(forms.ModelForm):
    new_password = forms.CharField(
        required=False,
        min_length=6,
        label='Set New Password (optional)',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Leave blank to keep current password'}),
    )
    confirm_password = forms.CharField(
        required=False,
        min_length=6,
        label='Confirm New Password',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Re-enter new password'}),
    )

    class Meta:
        model = CustomUser
        fields = [
            'roll_number', 'full_name', 'role',
            'student_class', 'semester', 'assigned_classes',
            'bio', 'is_active', 'must_change_password',
        ]
        widgets = {
            'roll_number': forms.TextInput(attrs={'class': 'form-control'}),
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'role': forms.Select(attrs={'class': 'form-select', 'id': 'id_edit_role'}),
            'student_class': forms.Select(attrs={'class': 'form-select'}),
            'semester': forms.Select(attrs={'class': 'form-select'}),
            'assigned_classes': forms.CheckboxSelectMultiple(attrs={'class': 'form-check-input'}),
            'bio': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Academic bio or notes...'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'must_change_password': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_roll_number(self):
        roll = (self.cleaned_data.get('roll_number') or '').strip().upper()
        if not roll:
            raise ValidationError('Roll Number / System ID is required.')
        qs = CustomUser.objects.filter(roll_number=roll)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError(f'Another user already uses Roll Number "{roll}".')
        return roll

    def clean(self):
        cleaned = super().clean()
        pwd1 = cleaned.get('new_password')
        pwd2 = cleaned.get('confirm_password')
        if pwd1 or pwd2:
            if pwd1 != pwd2:
                raise ValidationError('New passwords do not match.')
        return cleaned


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = [
            'subject', 'question_type', 'prompt',
            'option_a', 'option_b', 'option_c', 'option_d', 'correct_option',
            'correct_answer_text', 'model_answer',
            'marks', 'difficulty',
        ]
        widgets = {
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'question_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_question_type'}),
            'prompt': forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'id': 'id_prompt', 'placeholder': 'Enter question prompt. Use \\( ... \\) or $$ ... $$ for MathJax equations.'}),
            'option_a': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Option A'}),
            'option_b': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Option B'}),
            'option_c': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Option C'}),
            'option_d': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Option D'}),
            'correct_option': forms.Select(attrs={'class': 'form-select'}),
            'correct_answer_text': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Exact answer(s), comma-separated e.g. 42, forty two'}),
            'model_answer': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Reference answer for Short Answer & Answer Key'}),
            'marks': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'difficulty': forms.Select(attrs={'class': 'form-select'}),
        }

    def clean(self):
        cleaned = super().clean()
        qtype = cleaned.get('question_type')
        if qtype == Question.TYPE_MCQ:
            if not (cleaned.get('option_a') and cleaned.get('option_b')):
                raise ValidationError('MCQ questions require at least Option A and Option B.')
            if not cleaned.get('correct_option'):
                raise ValidationError('Please select the correct option (A, B, C, or D) for this MCQ.')
        elif qtype == Question.TYPE_FITB:
            if not cleaned.get('correct_answer_text'):
                raise ValidationError('Fill in the Blanks questions require at least one acceptable answer.')
        return cleaned


class ExamForm(forms.ModelForm):
    selected_questions = forms.ModelMultipleChoiceField(
        queryset=Question.objects.select_related('subject', 'subject__academic_class').all(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
        label='Select Questions from Question Bank',
    )

    class Meta:
        model = Exam
        fields = [
            'title', 'academic_class', 'subject', 'duration_minutes',
            'passing_percentage', 'max_tab_switches', 'instructions',
            'is_published', 'shuffle_questions',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Mid-Term Business Mathematics'}),
            'academic_class': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'duration_minutes': forms.NumberInput(attrs={'class': 'form-control', 'min': 5, 'max': 300}),
            'passing_percentage': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 100, 'step': '0.5'}),
            'max_tab_switches': forms.NumberInput(attrs={'class': 'form-control', 'min': 1, 'max': 20}),
            'instructions': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_published': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'shuffle_questions': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ['title', 'description', 'academic_class', 'subject', 'due_date', 'max_marks', 'attachment']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. Case Study on Market Equilibrium'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'academic_class': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.Select(attrs={'class': 'form-select'}),
            'due_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
            'max_marks': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'attachment': forms.FileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['due_date'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']


class AssignmentSubmissionForm(forms.ModelForm):
    class Meta:
        model = AssignmentSubmission
        fields = ['file', 'notes']
        widgets = {
            'file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.docx,.doc,.png,.jpg,.jpeg,.webp',
            }),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Optional submission notes for your teacher...'}),
        }


class AssignmentGradeForm(forms.ModelForm):
    class Meta:
        model = AssignmentSubmission
        fields = ['marks_obtained', 'feedback']
        widgets = {
            'marks_obtained': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.5', 'min': 0}),
            'feedback': forms.Textarea(attrs={'class': 'form-control', 'rows': 2, 'placeholder': 'Constructive feedback...'}),
        }


class MessageComposeForm(forms.Form):
    message_type = forms.ChoiceField(
        choices=Message.TYPE_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'id_message_type'}),
        label='Message Type',
    )
    recipient_roll_number = forms.CharField(
        required=False,
        max_length=50,
        label='Recipient Roll Number / System ID',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. BBA001 or TCH001',
            'list': 'rollNumberSuggestions',
        }),
    )
    target_class = forms.ModelChoiceField(
        queryset=AcademicClass.objects.all(),
        required=False,
        empty_label='All Classes (Global Notice)',
        label='Target Class / Department',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    subject = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Notice or message subject'}),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 5, 'placeholder': 'Write your message or announcement here...'}),
    )

    def clean(self):
        cleaned = super().clean()
        mtype = cleaned.get('message_type')
        roll = (cleaned.get('recipient_roll_number') or '').strip().upper()
        if mtype == Message.TYPE_DIRECT:
            if not roll:
                raise ValidationError('Please enter a Recipient Roll Number for Direct Messages.')
            try:
                recipient = CustomUser.objects.get(roll_number=roll, is_active=True)
                cleaned['recipient_user'] = recipient
            except CustomUser.DoesNotExist:
                raise ValidationError(f'No active user found with Roll Number "{roll}".')
        return cleaned


class PdfQuestionUploadForm(forms.Form):
    MODE_AUTO = 'auto'
    MODE_AI = 'ai'
    MODE_LOCAL = 'local'
    MODE_CHOICES = [
        (MODE_AUTO, 'Auto (Local Math Parser for text PDFs, Gemini AI for scanned PDFs)'),
        (MODE_AI, 'Gemini AI Math & Scanned PDF Recognition'),
        (MODE_LOCAL, 'Local PDF Math Symbol Parser (pypdf, offline)'),
    ]

    subject = forms.ModelChoiceField(
        queryset=Subject.objects.select_related('academic_class').all(),
        label='Target Subject',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    pdf_file = forms.FileField(
        label='Question Bank PDF (.pdf)',
        validators=[FileExtensionValidator(['pdf'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.pdf',
        }),
    )
    extraction_mode = forms.ChoiceField(
        choices=MODE_CHOICES,
        initial=MODE_AUTO,
        label='Recognition Engine',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class PaperFormatUploadForm(forms.Form):
    exam = forms.ModelChoiceField(
        queryset=Exam.objects.select_related('academic_class', 'subject').all(),
        label='Select Exam Questions',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    format_file = forms.FileField(
        label='Upload Paper Format Template (.docx, .pdf, or .txt)',
        validators=[FileExtensionValidator(['docx', 'pdf', 'txt'])],
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.docx,.pdf,.txt',
        }),
    )
    include_answer_key = forms.BooleanField(
        required=False,
        initial=False,
        label='Include Answer Key section at the end of Word & PDF files',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

