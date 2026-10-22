"""Object-oriented domain model: users/roles (inheritance + encapsulation) and campuses."""
from policies import get_policy


class User:
    """Base class. Attributes are private (encapsulation) and exposed via properties."""
    role = "user"
    role_label = "User"
    permissions = frozenset()

    def __init__(self, user_id, username, full_name, campus_id=None):
        self.__id = user_id
        self.__username = username
        self.__full_name = full_name
        self.__campus_id = campus_id

    @property
    def id(self):
        return self.__id

    @property
    def username(self):
        return self.__username

    @property
    def full_name(self):
        return self.__full_name

    @property
    def campus_id(self):
        return self.__campus_id

    def has_permission(self, permission):
        return permission in self.permissions

    def __repr__(self):
        return f"<{type(self).__name__} {self.__username}>"


class Lecturer(User):
    role = "lecturer"
    role_label = "Lecturer"
    permissions = frozenset({"book", "view_own_bookings", "cancel_own_booking", "view_resources"})


class CampusAdministrator(User):
    role = "campus_admin"
    role_label = "Campus Administrator"
    permissions = frozenset({"manage_resources", "view_campus_bookings",
                             "view_campus_reports", "view_resources"})


class SystemOperator(User):
    role = "system_operator"
    role_label = "System Operator"
    permissions = frozenset({"view_all_bookings", "view_all_resources", "view_campus_reports",
                             "compare_campuses", "view_resources"})


ROLE_CLASSES = {cls.role: cls for cls in (Lecturer, CampusAdministrator, SystemOperator)}


def user_from_row(row):
    return ROLE_CLASSES[row["role"]](row["id"], row["username"], row["full_name"], row["campus_id"])


class Campus:
    def __init__(self, campus_id, name, city, policy_key):
        self.id = campus_id
        self.name = name
        self.city = city
        self.policy_key = policy_key

    @property
    def policy(self):
        return get_policy(self.policy_key)

    def __str__(self):
        return self.name
