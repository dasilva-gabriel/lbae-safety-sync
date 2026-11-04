from functools import cache


class SafetyAction:
    def __init__(self, name, desc, site, action_uid, cache_id):
        self.name = name
        self.desc = desc
        self.site = site
        self.action_uid = action_uid
        self.cache_id = cache_id
        self.note = "/"

    def __str__(self):
        return (f"SafetyAction(Name: {self.name}, Description: {self.desc}, Note: {self.note}"
                f"Site: {self.site}, Action UID: {self.action_uid}) / Cache ID: {self.cache_id}")
