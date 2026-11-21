from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
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
)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ('roll_number', 'full_name', 'role', 'student_class', 'semester', 'must_change_password', 'is_active')
    list_filter = ('role', 'student_class', 'semester', 'must_change_password', 'is_active')
    search_fields = ('roll_number', 'full_name')
    ordering = ('roll_number',)

    fieldsets = (
        ('Credentials', {'fields': ('roll_number', 'password', 'must_change_password')}),
        ('Personal & Academic Info', {'fields': ('full_name', 'role', 'student_class', 'semester', 'assigned_classes', 'bio')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('roll_number', 'full_name', 'role', 'student_class', 'semester', 'password1', 'password2'),
        }),
    )


@admin.register(AcademicClass)
class AcademicClassAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'created_at')
    search_fields = ('name', 'code')


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'academic_class', 'teacher')
    list_filter = ('academic_class',)
    search_fields = ('code', 'name')


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('id', 'subject', 'question_type', 'marks', 'difficulty', 'created_by')
    list_filter = ('question_type', 'difficulty', 'subject')
    search_fields = ('prompt',)


class ExamQuestionInline(admin.TabularInline):
    model = ExamQuestion
    extra = 1


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ('title', 'academic_class', 'subject', 'duration_minutes', 'is_published', 'created_at')
    list_filter = ('academic_class', 'is_published')
    inlines = [ExamQuestionInline]


@admin.register(ExamAttempt)
class ExamAttemptAdmin(admin.ModelAdmin):
    list_display = ('exam', 'student', 'status', 'score', 'tab_switch_count', 'is_fully_graded', 'started_at')
    list_filter = ('status', 'is_fully_graded', 'exam')


admin.site.register(ExamAnswer)
admin.site.register(Assignment)
admin.site.register(AssignmentSubmission)
admin.site.register(AttendanceSession)
admin.site.register(AttendanceRecord)
admin.site.register(Message)
admin.site.register(MessageReadReceipt)
