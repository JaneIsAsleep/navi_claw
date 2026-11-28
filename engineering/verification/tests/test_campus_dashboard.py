from datetime import datetime
from zoneinfo import ZoneInfo

from navi.campus_dashboard import build_dashboard


NOW = datetime(2026, 11, 28, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_dashboard_extracts_messages_deadlines_assignments_and_exams():
    announcements = [
        {
            "id": "_1_1",
            "title": "[CS323] Project 5 Release Notification",
            "info": "发布者: 计算机系\n\n发布至: Compilers Fall 2026",
            "details": (
                "发布时间: 2026年11月22日 星期日 下午11时47分19秒 CST\n\n"
                "各位同学：\nCS323 Project 5 现已发布，请在截止时间前完成提交。\n"
                "OJ截止时间：2026 年 12 月 13 日 23:59。"
            ),
            "attachments": [],
        },
        {
            "id": "_2_1",
            "title": "Final Exam Arrangement",
            "info": "发布者: 计算机系\n\n发布至: Compilers Fall 2026",
            "details": (
                "发布时间: 2026年11月20日 星期五 上午09时00分00秒 CST\n\n"
                "The final exam will be held on December 20, 2026 at 09:00."
            ),
            "attachments": [],
        },
        {
            "id": "_3_1",
            "title": "Course welcome",
            "info": "发布者: 教师\n\n发布至: Object-oriented Analysis and Design Fall 2026",
            "details": (
                "发布时间: 2026年11月18日 星期三 下午03时00分00秒 CST\n\n"
                "Welcome to the course. Please read the syllabus."
            ),
            "attachments": [],
        },
    ]
    events = [
        {"text": "Sunday, November 29, 2026,\n11:55pResearch Report"},
    ]

    dashboard = build_dashboard(announcements, events, NOW)

    assert dashboard["recent_messages"][0]["title"] == "[CS323] Project 5 Release Notification"
    assert dashboard["recent_messages"][0]["kind"] == "assignment"
    assert dashboard["recent_messages"][0]["course"] == "Compilers Fall 2026"
    assert dashboard["recent_messages"][0]["url"] == ""
    assert [item["title"] for item in dashboard["deadlines"]] == [
        "Research Report",
        "[CS323] Project 5 Release Notification",
        "Final Exam Arrangement",
    ]
    assert "assignments" not in dashboard
    assert "exams" not in dashboard
    assert dashboard["summary"] == {
        "courses": 2,
        "messages": 3,
        "deadlines": 3,
    }


def test_dashboard_ignores_past_calendar_events():
    events = [
        {"text": "Wednesday, November 11, 2026,\n11:59pPast Assignment"},
        {"text": "Sunday, November 29, 2026,\n11:55pResearch Report"},
    ]
    dashboard = build_dashboard([], events, NOW)
    assert [item["title"] for item in dashboard["deadlines"]] == ["Research Report"]


def test_dashboard_infers_year_from_old_announcement_and_filters_expired_work():
    announcements = [{
        "id": "_2025_1",
        "title": "Deadline for Assignment 5 postponed to Dec 28",
        "info": "发布者: 教师\n\n发布至: Discrete Mathematics Fall 2025",
        "details": (
            "发布时间: 2025年12月1日 星期一 上午09时00分00秒 CST\n\n"
            "The deadline for Assignment 5 is postponed to Dec 28 at 23:59."
        ),
        "attachments": [],
    }]
    dashboard = build_dashboard(announcements, [], NOW)
    assert dashboard["deadlines"] == []


def test_dashboard_collapses_multiple_times_for_same_event():
    announcements = [{
        "id": "_exam_1",
        "title": "课程答辩（12月24日，14:30-16:30/17:00）",
        "info": "发布者: 教师\n\n发布至: CSE 2026 Fall Group Project",
        "details": (
            "发布时间: 2026年11月12日 星期四 下午03时08分19秒 CST\n\n"
            "课程答辩定于12月24日，14:30-16:30/17:00进行。"
        ),
        "attachments": [],
    }]
    dashboard = build_dashboard(announcements, [], NOW)
    assert len(dashboard["deadlines"]) == 1
    assert dashboard["deadlines"][0]["due_label"] == "12月24日 14:30"
