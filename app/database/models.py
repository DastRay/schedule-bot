"""
Определяет ORM-модели SQLAlchemy для хранения информации
о факультетах, группах, временных слотах и занятиях.

- Faculty: факультет
- Group: учебная группа
- Lesson: пары студентов
- Users: пользователь
- Professor: преподаватели
- ProfessorLesson: пары препподавателей
"""

from sqlalchemy import (
    Column, Integer, String, ForeignKey, Text, UniqueConstraint
)

from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class User(Base):
    """
    Пользователь

    Поля:
    id : Integer
        Первичный ключ (берется id пользователя его телеграм аккаунта)
    group_id : Integer
        Внешний ключ на группу.
   faculty_id : Integer
        Внешний ключ на факультет.
    role : Integer
        Роль пользователя (0-9), по умолчанию 0.
        0 - пользователь
        1 - админ
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)  # Telegram user_id
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    faculty_id = Column(Integer, ForeignKey("faculties.id", ondelete="SET NULL"), nullable=True, index=True)
    role = Column(Integer, nullable=False, default=0)

    group = relationship("Group", lazy="joined")
    faculty = relationship("Faculty", lazy="joined")

    __table_args__ = (
        UniqueConstraint('id', name='uq_user_id'),
    )

class Faculty(Base):
    """
    Модель факультета.

    Поля:
    id : Integer
        Первичный ключ.
    name : String
        Название факультета (уникальное).
    """

    __tablename__ = "faculties"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class Group(Base):
    """
    Модель группы.

    Поля:
    id : Integer
        Первичный ключ.
    group_name : str
        Название группы (уникальное).
    faculty_id : Integer | None
        Внешний ключ на факультет.
    faculty : Faculty
        ORM-связь (joined-load).
    """

    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    group_name = Column(String, unique=True, nullable=False, index=True)
    faculty_id = Column(Integer, ForeignKey("faculties.id", ondelete="SET NULL"), nullable=True)
    faculty = relationship("Faculty", lazy="joined")


class Lesson(Base):
    """
    Модель занятия (конкретное расписание).

    Поля:
    id : Integer
        Первичный ключ.
    group_id : Integer
        Внешний ключ на группу.
    weekday : Integer | None
        День недели (1–7).
    lesson_number : int | None
        Номер пары.
    subject : String | None
        Предмет.
    professors : String | None
        Преподаватели.
    rooms : String | None
        Аудитории.
    week_mark : WeekMarkEnum | None
        Маркер недели (every/plus/minus).
    type : String | None
        Тип занятия (лекция, практика и т.д.)
    """

    __tablename__ = "lessons"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    weekday = Column(Integer, nullable=True)
    lesson_number = Column(Integer, nullable=True)
    subject = Column(Text, nullable=True)
    professors = Column(Text, nullable=True)
    rooms = Column(Text, nullable=True)
    week_mark = Column(String, nullable=True)
    type = Column(String, nullable=True)
    
    __table_args__ = (
        UniqueConstraint(
            'group_id', 'weekday', 'lesson_number', 'subject',
            'professors', 'rooms', 'week_mark', 'type',
            name='uq_lesson_unique'
        ),
    )


class Professor(Base):
    """
    Таблица преподавателя.

    Поля:
    id : Integer
        Первичный ключ.
    name : String
        Имя и инициалы преподавателя (формат "Фамилия И О").
    """

    __tablename__ = "professors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True, index=True)

    __table_args__ = (
        UniqueConstraint('name', name='uq_professor_name'),
    )


class ProfessorLesson(Base):
    """
    Таблица расписания преподавателя.

    Позволяет хранить расписание занятий конкретного преподавателя.

    Поля:
    id : Integer
        Первичный ключ.
    professor_id : Integer
        Преподаватель.
    weekday : Integer
        День недели (1–7).
    lesson_number : Integer
        Номер пары.
    subject : String
        Предмет.
    rooms : String | None
        Аудитория.
    week_mark : String | None
        Маркер недели.
    """

    __tablename__ = "professor_lessons"
    id = Column(Integer, primary_key=True)
    professor_id = Column(Integer, ForeignKey("professors.id", ondelete="CASCADE"), nullable=False)
    weekday = Column(Integer, nullable=True)
    lesson_number = Column(Integer, nullable=True)
    subject = Column(Text, nullable=False)
    rooms = Column(Text, nullable=True)
    week_mark = Column(String, nullable=True)

    professor = relationship("Professor", lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            'professor_id', 'weekday', 'lesson_number', 'subject', 'rooms', 'week_mark',
            name='uq_professor_lesson_unique'
        ),
    )