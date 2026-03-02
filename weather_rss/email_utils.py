import smtplib
from email.message import EmailMessage
import logging
import os

LOG_FILE = "/home/lh_admin/weather_rss/logs/email.log"
logging.basicConfig(filename=LOG_FILE,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')

def send_success_email():
    try:
        EMAIL_USER = os.environ.get("EMAIL_USER")
        EMAIL_PASS = os.environ.get("EMAIL_PASS")

        msg = EmailMessage()
        msg['Subject'] = "Weather RSS Success"
        msg['From'] = EMAIL_USER
        msg['To'] = "recipient@example.com"  # Replace with your email
        msg.set_content("Weather RSS feed processed successfully.")

        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(EMAIL_USER, EMAIL_PASS)
            smtp.send_message(msg)

        logging.info("Weather RSS email sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send Weather RSS email: {e}")
