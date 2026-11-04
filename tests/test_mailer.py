from safetysync import mailer as mailer_module
from safetysync.config import SmtpSettings
from safetysync.mailer import SmtpMailer


class FakeSMTP:
    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send", msg))


def test_envoi_smtp_starttls(monkeypatch):
    monkeypatch.setattr(mailer_module.smtplib, "SMTP", FakeSMTP)
    settings = SmtpSettings(
        smtp_user="user@smtp-brevo.com",
        smtp_password="secret",
        email_from="lbae@exemple.fr",
        _env_file=None,
    )
    SmtpMailer(settings).send(
        ["a@x.fr"], "Sujet", "<p>Bonjour<br>tous</p>", cc=["b@x.fr", "a@x.fr"]
    )
    server = FakeSMTP.instances[-1]
    assert (server.host, server.port) == ("smtp-relay.brevo.com", 587)
    assert server.calls[0] == "starttls"
    assert server.calls[1] == ("login", "user@smtp-brevo.com", "secret")
    msg = server.calls[2][1]
    assert msg["From"] == "Safety-Sync <lbae@exemple.fr>"
    assert msg["To"] == "a@x.fr" and msg["Cc"] == "b@x.fr"
    assert msg.get_body(("html",)).get_content().strip() == "<p>Bonjour<br>tous</p>"
    assert "Bonjour" in msg.get_body(("plain",)).get_content()


def test_logo_integre():
    settings = SmtpSettings(smtp_user="u", smtp_password="p", email_from="a@b.fr", _env_file=None)
    msg = SmtpMailer(settings).build(
        ["x@y.fr"], "S", '<img src="cid:logo">', images={"logo": b"\x89PNG"}
    )
    parts = {p.get_content_type(): p for p in msg.walk()}
    assert "multipart/related" in parts
    assert parts["image/png"]["Content-ID"] == "<logo>"
    assert parts["image/png"].get_content() == b"\x89PNG"
