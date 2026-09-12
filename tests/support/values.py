class IdentityOnly:
    def __eq__(self, other: object) -> bool:
        raise AssertionError("Declaration comparison must use identity")
