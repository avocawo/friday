class FridayError(Exception):
    """An expected failure with a stable code. The message is an English template; its parameters fill it, so the
    English text is what gets stored and logged, and i18n.error shows it in the person's language."""

    def __init__(self, code, message, **params):
        self.code = code
        self.template = message
        self.params = params
        super().__init__(message.format(**params) if params else message)
