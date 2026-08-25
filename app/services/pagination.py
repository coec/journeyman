"""Shared per-user list pagination helpers."""

import math
from dataclasses import dataclass

from flask import request

from app.services.user_preferences import get_or_create_user_preferences

PAGE_SIZE_CHOICES = (25, 50, 100, 200)
DEFAULT_PAGE_SIZE = 50


def page_size_for_user(username):
    preferences = get_or_create_user_preferences(username)
    value = int(preferences.rows_per_page or DEFAULT_PAGE_SIZE)
    return value if value in PAGE_SIZE_CHOICES else DEFAULT_PAGE_SIZE


@dataclass
class ListPagination:
    items: list
    page: int
    per_page: int
    total: int
    pages: int

    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    @property
    def prev_num(self):
        return self.page - 1 if self.has_prev else None

    @property
    def next_num(self):
        return self.page + 1 if self.has_next else None


def paginate_list(rows, per_page):
    rows = list(rows)
    page = max(request.args.get("page", 1, type=int) or 1, 1)
    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(page, pages)
    start = (page - 1) * per_page
    return ListPagination(rows[start:start + per_page], page, per_page, total, pages)
