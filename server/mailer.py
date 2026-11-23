"""Transactional e-mail for two-factor codes. Configuration is read from the environment on every send."""
import html
import os
import smtplib
import socket
import ssl
import threading
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr

TIMEOUT = 5  # per socket operation
DEADLINE = 8  # whole delivery; the menu aborts its request after 10 s
LANGS = ('ru', 'en', 'es')

SUBJECTS = {
    'en': {'login': 'Your NULL CITY sign-in code', 'enable': 'Confirm two-factor authentication', 'disable': 'Confirm turning off two-factor authentication'},
    'ru': {'login': 'Код входа в NULL CITY', 'enable': 'Подтверждение двухфакторной защиты', 'disable': 'Подтверждение отключения двухфакторной защиты'},
    'es': {'login': 'Tu código de acceso a NULL CITY', 'enable': 'Confirma la verificación en dos pasos', 'disable': 'Confirma la desactivación de la verificación en dos pasos'},
}
BODIES = {
    'en': ('Your code is: {code}', 'It expires in {minutes} minutes. If this was not you, change your password and ignore this message. Never share this code.'),
    'ru': ('Ваш код: {code}', 'Он действует {minutes} минут. Если это были не вы, смените пароль и проигнорируйте письмо. Никому не сообщайте этот код.'),
    'es': ('Tu código es: {code}', 'Caduca en {minutes} minutos. Si no fuiste tú, cambia tu contraseña e ignora este mensaje. No compartas este código.'),
}

# GDPR confirmations: subject, headline, note.
NOTICES = {
    'en': {'export': ('Your NULL CITY data was exported', 'A copy of your personal data has just been downloaded from your account.', 'If this was not you, change your password and turn on two-factor authentication.'),
           'erasure': ('Your NULL CITY account was deleted', 'Your account and personal data have been deleted.', 'This cannot be undone. This is the last message you will receive from us about this account.')},
    'ru': {'export': ('Ваши данные NULL CITY выгружены', 'Копия ваших персональных данных только что выгружена из вашего аккаунта.', 'Если это были не вы, смените пароль и включите двухфакторную защиту.'),
           'erasure': ('Ваш аккаунт NULL CITY удалён', 'Ваш аккаунт и персональные данные удалены.', 'Это действие необратимо. Это последнее письмо об этом аккаунте.')},
    'es': {'export': ('Tus datos de NULL CITY se han exportado', 'Se acaba de descargar una copia de tus datos personales desde tu cuenta.', 'Si no fuiste tú, cambia tu contraseña y activa la verificación en dos pasos.'),
           'erasure': ('Tu cuenta de NULL CITY se ha eliminado', 'Tu cuenta y tus datos personales se han eliminado.', 'Esta acción es irreversible. Este es el último mensaje sobre esta cuenta.')},
}


class MailError(Exception):
    """Delivery is unavailable or failed. Never carries the code or credentials."""


def enabled():
    return bool(os.environ.get('SMTP_HOST', '').strip())


def envelope(to, subject):
    sender = os.environ.get('SMTP_FROM') or 'NULL CITY <no-reply@nullcity.local>'
    message = EmailMessage()  # default policy rejects line breaks in headers
    try:
        parsed = getaddresses([to])
        if len(parsed) != 1 or not parsed[0][1] or parsed[0][1] != to.strip():
            raise ValueError('Exactly one plain address is required')
        name, address = parseaddr(sender)
        message['From'] = formataddr((name, address)) if address else sender
        message['To'] = to
        message['Subject'] = subject
    except (ValueError, TypeError) as error:
        raise MailError('Invalid address') from error
    return message


def build(to, code, lang='en', purpose='login', minutes=10):
    lang = lang if lang in LANGS else 'en'
    purpose = purpose if purpose in SUBJECTS[lang] else 'login'
    headline, note = (part.format(code=code, minutes=minutes) for part in BODIES[lang])
    message = envelope(to, SUBJECTS[lang][purpose])
    message.set_content(headline + '\n\n' + note + '\n')
    message.add_alternative(
        '<html><body style="font-family:sans-serif"><p>' + html.escape(headline.replace(code, '')) +
        '<b style="font-size:24px;letter-spacing:4px">' + html.escape(code) + '</b></p><p>' + html.escape(note) + '</p></body></html>',
        subtype='html')
    return message


def send(to, code, lang='en', purpose='login', minutes=10):
    deliver(build(to, code, lang, purpose, minutes))


def notify(to, kind, lang='en'):
    subject, headline, note = NOTICES[lang if lang in LANGS else 'en'][kind]
    message = envelope(to, subject)
    message.set_content(headline + '\n\n' + note + '\n')
    deliver(message)


def deliver(message):
    host = os.environ.get('SMTP_HOST', '').strip()
    if not host:
        raise MailError('SMTP is not configured')
    mode = (os.environ.get('SMTP_TLS') or 'starttls').strip().lower()
    if mode not in ('starttls', 'ssl', 'none'):
        raise MailError('Unknown SMTP_TLS mode')
    try:
        port = int(os.environ.get('SMTP_PORT') or 1025)
    except ValueError as error:
        raise MailError('Invalid SMTP_PORT') from error
    user, password = os.environ.get('SMTP_USER', ''), os.environ.get('SMTP_PASSWORD', '')
    expired = threading.Event()
    client = None

    def abort():  # hard total deadline: closing the socket wakes any blocked read or write
        expired.set()
        try:
            client.sock.shutdown(socket.SHUT_RDWR)
        except (OSError, AttributeError):
            pass

    watchdog = threading.Timer(DEADLINE, abort)
    watchdog.daemon = True
    try:
        context = ssl.create_default_context(cafile=os.environ.get('SMTP_CA') or None) if mode != 'none' else None
        client = smtplib.SMTP_SSL(timeout=TIMEOUT, context=context) if mode == 'ssl' else smtplib.SMTP(timeout=TIMEOUT)
        client._host = host  # smtplib checks the certificate against this; connect() alone leaves it empty
        watchdog.start()
        with client:
            client.connect(host, port)
            client.ehlo()
            if mode == 'starttls':
                client.starttls(context=context)  # raises when unsupported: never downgrade silently
                client.ehlo()
            if user and mode != 'none':  # credentials are never sent over a clear channel
                client.login(user, password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ssl.SSLError, ValueError) as error:
        raise MailError('Timeout' if expired.is_set() else type(error).__name__) from None
    finally:
        watchdog.cancel()
