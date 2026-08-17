class PreserveStoredFieldsMixin:
    """ModelForm 编辑敏感字段时，空输入沿用实例原值。"""

    preserved_fields = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        exists = bool(self.instance and self.instance.pk)
        self._stored_values = {
            field: getattr(self.instance, field) if exists else "" for field in self.preserved_fields
        }

    def preserved_value(self, field):
        return self.cleaned_data.get(field) or self._stored_values[field]
