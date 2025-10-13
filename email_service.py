"""
Service d'envoi d'emails avec AnyMail et Mailgun
"""
import os
import random
import string
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
import logging

logger = logging.getLogger(__name__)

class EmailService:
    """Service pour l'envoi d'emails avec AnyMail et Mailgun"""
    
    def __init__(self):
        self.from_email = os.environ.get('MAILGUN_FROM_EMAIL', 'noreply@mail.revisia-app.fr')
    
    def generate_verification_code(self, length=6):
        """Génère un code de vérification numérique"""
        return ''.join(random.choices(string.digits, k=length))
    
    def send_verification_email(self, user_email, verification_code):
        """Envoie un email de vérification avec le code"""
        try:
            subject = "Vérification de votre adresse email - Révisia"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Vérification Email - Révisia</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        line-height: 1.6;
                        color: #333;
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 20px;
                        background-color: #f4f4f4;
                    }}
                    .container {{
                        background-color: white;
                        padding: 30px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        text-align: center;
                        margin-bottom: 30px;
                    }}
                    .logo {{
                        font-size: 28px;
                        font-weight: bold;
                        color: #4f46e5;
                        margin-bottom: 10px;
                    }}
                    .code-container {{
                        background-color: #f8fafc;
                        border: 2px dashed #4f46e5;
                        border-radius: 8px;
                        padding: 20px;
                        text-align: center;
                        margin: 20px 0;
                    }}
                    .verification-code {{
                        font-size: 32px;
                        font-weight: bold;
                        color: #4f46e5;
                        letter-spacing: 5px;
                        font-family: 'Courier New', monospace;
                    }}
                    .instructions {{
                        background-color: #fef3c7;
                        border-left: 4px solid #f59e0b;
                        padding: 15px;
                        margin: 20px 0;
                        border-radius: 4px;
                    }}
                    .footer {{
                        text-align: center;
                        margin-top: 30px;
                        color: #6b7280;
                        font-size: 14px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <div class="logo">📚 Révisia</div>
                        <h1>Vérification de votre adresse email</h1>
                    </div>
                    
                    <p>Bonjour,</p>
                    
                    <p>Pour finaliser votre inscription sur Révisia, veuillez vérifier votre adresse email en utilisant le code suivant :</p>
                    
                    <div class="code-container">
                        <div class="verification-code">{verification_code}</div>
                    </div>
                    
                    <div class="instructions">
                        <strong>Instructions :</strong>
                        <ul>
                            <li>Ce code est valide pendant 15 minutes</li>
                            <li>Vous pouvez faire jusqu'à 3 tentatives</li>
                            <li>Si vous n'avez pas demandé cette vérification, ignorez cet email</li>
                        </ul>
                    </div>
                    
                    <p>Si vous rencontrez des difficultés, n'hésitez pas à nous contacter.</p>
                    
                    <div class="footer">
                        <p>Cet email a été envoyé automatiquement, merci de ne pas y répondre.</p>
                        <p>© 2024 Révisia - Générateur de QCM IA</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
            Vérification de votre adresse email - Révisia
            
            Bonjour,
            
            Pour finaliser votre inscription sur Révisia, veuillez vérifier votre adresse email en utilisant le code suivant :
            
            Code de vérification : {verification_code}
            
            Instructions :
            - Ce code est valide pendant 15 minutes
            - Vous pouvez faire jusqu'à 3 tentatives
            - Si vous n'avez pas demandé cette vérification, ignorez cet email
            
            Si vous rencontrez des difficultés, n'hésitez pas à nous contacter.
            
            © 2024 Révisia - Générateur de QCM IA
            """
            
            # Envoi de l'email via Django send_mail avec AnyMail
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=self.from_email,
                to=[user_email]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            logger.info(f"Email de vérification envoyé à {user_email}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email à {user_email}: {str(e)}")
            return False
    
    def send_welcome_email(self, user_email, user_name):
        """Envoie un email de bienvenue après vérification"""
        try:
            subject = "Bienvenue sur Révisia ! 🎉"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Bienvenue - Révisia</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        line-height: 1.6;
                        color: #333;
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 20px;
                        background-color: #f4f4f4;
                    }}
                    .container {{
                        background-color: white;
                        padding: 30px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        text-align: center;
                        margin-bottom: 30px;
                    }}
                    .logo {{
                        font-size: 28px;
                        font-weight: bold;
                        color: #4f46e5;
                        margin-bottom: 10px;
                    }}
                    .success-badge {{
                        background-color: #10b981;
                        color: white;
                        padding: 10px 20px;
                        border-radius: 20px;
                        display: inline-block;
                        margin: 20px 0;
                    }}
                    .features {{
                        background-color: #f8fafc;
                        padding: 20px;
                        border-radius: 8px;
                        margin: 20px 0;
                    }}
                    .feature-item {{
                        margin: 10px 0;
                        padding-left: 20px;
                        position: relative;
                    }}
                    .feature-item::before {{
                        content: "✓";
                        position: absolute;
                        left: 0;
                        color: #10b981;
                        font-weight: bold;
                    }}
                    .cta-button {{
                        display: inline-block;
                        background-color: #4f46e5;
                        color: white;
                        padding: 12px 24px;
                        text-decoration: none;
                        border-radius: 6px;
                        margin: 20px 0;
                    }}
                    .footer {{
                        text-align: center;
                        margin-top: 30px;
                        color: #6b7280;
                        font-size: 14px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <div class="logo">📚 Révisia</div>
                        <h1>Bienvenue sur Révisia !</h1>
                        <div class="success-badge">✅ Email vérifié avec succès</div>
                    </div>
                    
                    <p>Bonjour {user_name},</p>
                    
                    <p>Félicitations ! Votre compte Révisia a été créé avec succès et votre adresse email a été vérifiée.</p>
                    
                    <div class="features">
                        <h3>🚀 Découvrez les fonctionnalités de Révisia :</h3>
                        <div class="feature-item">Générez des QCM à partir de vos documents</div>
                        <div class="feature-item">Questions adaptées à votre niveau</div>
                        <div class="feature-item">Suivi de vos progrès</div>
                        <div class="feature-item">Interface intuitive et moderne</div>
                    </div>
                    
                    <p>Vous pouvez maintenant commencer à utiliser Révisia en uploadant votre premier document.</p>
                    
                    <div style="text-align: center;">
                        <a href="https://revisia-app.fr" class="cta-button">Commencer maintenant</a>
                    </div>
                    
                    <p>Si vous avez des questions, n'hésitez pas à nous contacter.</p>
                    
                    <div class="footer">
                        <p>Cet email a été envoyé automatiquement, merci de ne pas y répondre.</p>
                        <p>© 2024 Révisia - Générateur de QCM IA</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
            Bienvenue sur Révisia !
            
            Bonjour {user_name},
            
            Félicitations ! Votre compte Révisia a été créé avec succès et votre adresse email a été vérifiée.
            
            Découvrez les fonctionnalités de Révisia :
            ✓ Générez des QCM à partir de vos documents
            ✓ Questions adaptées à votre niveau
            ✓ Suivi de vos progrès
            ✓ Interface intuitive et moderne
            
            Vous pouvez maintenant commencer à utiliser Révisia en uploadant votre premier document.
            
            Commencer maintenant : https://revisia-app.fr
            
            Si vous avez des questions, n'hésitez pas à nous contacter.
            
            © 2024 Révisia - Générateur de QCM IA
            """
            
            # Envoi de l'email via Django send_mail avec AnyMail
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=self.from_email,
                to=[user_email]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            logger.info(f"Email de bienvenue envoyé à {user_email}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de l'email de bienvenue à {user_email}: {str(e)}")
            return False
    
    def send_new_user_notification(self, user_email, user_name, username):
        """Envoie une notification à l'admin quand un nouvel utilisateur s'inscrit"""
        try:
            admin_email = "pierre.forques@viacesi.fr"
            subject = "🎉 Nouvel utilisateur inscrit sur Révisia"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Nouvel utilisateur - Révisia</title>
                <style>
                    body {{
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                        line-height: 1.6;
                        color: #333;
                        max-width: 600px;
                        margin: 0 auto;
                        padding: 20px;
                        background-color: #f4f4f4;
                    }}
                    .container {{
                        background-color: white;
                        padding: 30px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    }}
                    .header {{
                        text-align: center;
                        margin-bottom: 30px;
                    }}
                    .logo {{
                        font-size: 28px;
                        font-weight: bold;
                        color: #4f46e5;
                        margin-bottom: 10px;
                    }}
                    .notification-badge {{
                        background-color: #f59e0b;
                        color: white;
                        padding: 10px 20px;
                        border-radius: 20px;
                        display: inline-block;
                        margin: 20px 0;
                    }}
                    .user-info {{
                        background-color: #f8fafc;
                        padding: 20px;
                        border-radius: 8px;
                        margin: 20px 0;
                        border-left: 4px solid #4f46e5;
                    }}
                    .info-item {{
                        margin: 10px 0;
                        padding: 5px 0;
                    }}
                    .info-label {{
                        font-weight: bold;
                        color: #4f46e5;
                    }}
                    .footer {{
                        text-align: center;
                        margin-top: 30px;
                        color: #6b7280;
                        font-size: 14px;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <div class="logo">📚 Révisia</div>
                        <h1>Nouvel utilisateur inscrit !</h1>
                        <div class="notification-badge">🎉 Inscription confirmée</div>
                    </div>
                    
                    <p>Bonjour Pierre,</p>
                    
                    <p>Un nouvel utilisateur vient de s'inscrire sur Révisia :</p>
                    
                    <div class="user-info">
                        <div class="info-item">
                            <span class="info-label">📧 Email :</span> {user_email}
                        </div>
                        <div class="info-item">
                            <span class="info-label">👤 Nom complet :</span> {user_name}
                        </div>
                        <div class="info-item">
                            <span class="info-label">🏷️ Nom d'utilisateur :</span> {username}
                        </div>
                        <div class="info-item">
                            <span class="info-label">📅 Date d'inscription :</span> {timezone.now().strftime('%d/%m/%Y à %H:%M')}
                        </div>
                    </div>
                    
                    <p>L'utilisateur doit maintenant vérifier son email pour accéder à l'application.</p>
                    
                    <div class="footer">
                        <p>Cet email a été envoyé automatiquement par Révisia.</p>
                        <p>© 2024 Révisia - Générateur de QCM IA</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
            Nouvel utilisateur inscrit sur Révisia !
            
            Bonjour Pierre,
            
            Un nouvel utilisateur vient de s'inscrire sur Révisia :
            
            📧 Email : {user_email}
            👤 Nom complet : {user_name}
            🏷️ Nom d'utilisateur : {username}
            📅 Date d'inscription : {timezone.now().strftime('%d/%m/%Y à %H:%M')}
            
            L'utilisateur doit maintenant vérifier son email pour accéder à l'application.
            
            © 2024 Révisia - Générateur de QCM IA
            """
            
            # Envoi de l'email via Django send_mail avec AnyMail
            email = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=self.from_email,
                to=[admin_email]
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
            
            logger.info(f"Notification nouvel utilisateur envoyée à {admin_email} pour {user_email}")
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi de la notification admin pour {user_email}: {str(e)}")
            return False

# Instance globale du service email
email_service = EmailService()
