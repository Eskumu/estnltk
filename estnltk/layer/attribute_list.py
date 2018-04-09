class AttributeList:
    """Immutable lists for representing single attribute value of unambiguous layer."""
    def __init__(self, attr_list: list):
        self._list = attr_list

    def __getitem__(self, item):
        return self._list[item]

    def __repr__(self):
        return repr(self._list)

    def __str__(self):
        return str(self._list)
