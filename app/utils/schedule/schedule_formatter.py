import re
from collections import defaultdict

MAX_MESSAGE_LENGTH = 4000

lesson_num_emoji = {
    0: "1️⃣", 1: "2️⃣", 2: "3️⃣",
    3: "4️⃣", 4: "5️⃣", 5: "6️⃣", 6: "7️⃣"
}

weekday_names = {
    1: "Понедельник",
    2: "Вторник",
    3: "Среда",
    4: "Четверг",
    5: "Пятница",
    6: "Суббота",
    7: "Воскресенье"
}

lessonTimeData = {
    0: {"start": "08:30", "end": "10:05"},
    1: {"start": "10:15", "end": "11:50"},
    2: {"start": "12:10", "end": "13:45"},
    3: {"start": "14:00", "end": "15:35"},
    4: {"start": "15:55", "end": "17:30"},
    5: {"start": "17:45", "end": "19:20"},
    6: {"start": "19:30", "end": "21:00"}
}

url_pattern = re.compile(r"(https?://\S+)")

def get_lesson_time(lesson_number):
    if lesson_number in lessonTimeData:
        lesson = lessonTimeData[lesson_number]
        return lesson["start"], lesson["end"]
    else:
        return "❓❓:❓❓", "❓❓:❓❓"

def escape_md_v2(text: str) -> str:
    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    return ''.join(f'\\{c}' if c in escape_chars else c for c in text)

def format_schedule(lessons, week: str, header_prefix: str = "📅 Расписание"):
    """
    Универсальное форматирование расписания в стиле MarkdownV2.
    """

    header_prefix = f"*{escape_md_v2(header_prefix)}*"

    if week == "plus":
        filtered_lessons = [l for l in lessons if l.week_mark in ("plus", "every", None)]
    elif week == "minus":
        filtered_lessons = [l for l in lessons if l.week_mark in ("minus", "every", None)]
    else:
        filtered_lessons = lessons[:]  # "full" — без фильтра

    if not filtered_lessons:
        return []

    def format_lesson(l):
        start, end = get_lesson_time(lesson_number=l.lesson_number)
        time_str = f"⏳ {start} \\- {end}"

        lesson_num = lesson_num_emoji.get(l.lesson_number, "❓")

        rooms_text = l.rooms or "Место проведения не указано"
        urls = url_pattern.findall(rooms_text)
        if urls:
            rooms_text = url_pattern.sub(lambda m: f"[нажмите для подключения]({m.group(0)})", rooms_text)
        else:
            rooms_text = escape_md_v2(rooms_text)
        room = f"📍{rooms_text}"

        professors = ", ".join(l.professors) if isinstance(l.professors, list) else (l.professors or "Преподаватель не указан")
        professors = escape_md_v2(professors)

        subject = escape_md_v2(l.subject or "Предмет не указан")

        marker = {"plus": "➕", "minus": "➖", "every": "⚪"}.get(l.week_mark or "every", "⚪")

        return f"  {marker} {lesson_num} {subject}\n  👨‍🏫 {professors}\n  {room}\n  {time_str}"

    lessons_by_day = defaultdict(list)
    for l in filtered_lessons:
        if l.weekday is not None:
            lessons_by_day[l.weekday].append(l)

    header = {
        "plus": f"{header_prefix}\nНеделя ➕\n\n",
        "minus": f"{header_prefix}\nНеделя ➖\n\n",
        "full": f"{header_prefix}\nПолное расписание:\n\n"
    }.get(week, f"{header_prefix}\n\n")

    day_texts = []
    for wd in sorted(lessons_by_day.keys()):
        day_lessons = sorted(lessons_by_day[wd], key=lambda x: x.lesson_number or 0)
        day_block = f"🗓 *{escape_md_v2(weekday_names[wd])}*:\n" + "\n\n".join(format_lesson(l) for l in day_lessons) + "\n\n\n"
        day_texts.append(day_block)

    messages = []
    current_text = header
    for day_text in day_texts:
        if len(current_text) + len(day_text) > MAX_MESSAGE_LENGTH:
            messages.append(current_text)
            current_text = day_text
        else:
            current_text += day_text
    if current_text:
        messages.append(current_text)

    return messages