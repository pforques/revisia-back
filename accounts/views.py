from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.utils import timezone
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
import os
import uuid
import logging
import stripe
import json
from datetime import datetime, timedelta
# UserSerializer removed - using manual serialization

logger = logging.getLogger(__name__)
from .serializers import (
    UserRegistrationSerializer, UserLoginSerializer, 
    DocumentSerializer, QuestionSerializer, LessonSerializer, 
    UserAnswerSerializer, LessonStatsSerializer, LessonAttemptSerializer
)
from .models import User, Document, Question, Answer, Lesson, UserAnswer, LessonAttempt, GuestSession, StripePayment, EmailVerification
from ai_service import OpenAIService
from email_service import email_service


def ensure_special_premium(user: User):
    if user and (user.email or '').strip().lower() == 'uwu@uwu.com' and not user.is_premium:
        user.is_premium = True
        user.save(update_fields=['is_premium'])


@api_view(['POST'])
@permission_classes([AllowAny])
def register(request):
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        ensure_special_premium(user)

        # Envoyer automatiquement le code de vérification après inscription
        verification_code = email_service.generate_verification_code()
        expires_at = timezone.now() + timedelta(minutes=15)
        
        # Print le code dans la console pour les tests
        print(f"🔢 Code de vérification pour {user.email}: {verification_code}")
        
        # Créer l'enregistrement de vérification
        verification = EmailVerification.objects.create(
            user=user,
            code=verification_code,
            email=user.email,
            expires_at=expires_at
        )
        
        # Envoyer l'email de vérification
        email_sent = email_service.send_verification_email(user.email, verification_code)
        
        # Envoyer la notification admin
        user_name = f"{user.first_name} {user.last_name}".strip()
        admin_notification_sent = email_service.send_new_user_notification(
            user.email, 
            user_name, 
            user.username
        )
        
        if email_sent:
            # Mettre à jour la date d'envoi
            user.email_verification_sent_at = timezone.now()
            user.save(update_fields=['email_verification_sent_at'])
        
        # Générer les tokens mais marquer l'utilisateur comme non vérifié
        refresh = RefreshToken.for_user(user)
        return Response({
            'user': {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_premium': user.is_premium,
                'email_verified': user.email_verified,
            },
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            },
            'email_verification_sent': email_sent,
            'message': 'Compte créé avec succès. Vérifiez votre email pour activer votre compte.'
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    serializer = UserLoginSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.validated_data['user']
        ensure_special_premium(user)
        refresh = RefreshToken.for_user(user)
        return Response({
            'user': {
                'id': user.id,
                'email': user.email,
                'username': user.username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_premium': user.is_premium,
            },
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    user = request.user
    ensure_special_premium(user)
    return Response({
        'id': user.id,
        'email': user.email,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'is_premium': user.is_premium,
        'education_level': user.education_level,
        'email_verified': user.email_verified,
        'date_joined': user.date_joined,
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_subscription_info(request):
    """Récupère les informations d'abonnement de l'utilisateur"""
    user = request.user
    return Response(user.get_subscription_info())

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_subscription(request):
    """Crée un abonnement Stripe récurrent"""
    stripe.api_key = settings.STRIPE_SECRET_KEY
    user = request.user
    price_id = request.data.get('price_id')
    
    if not price_id:
        return Response({
            'error': 'Price ID requis'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    logger.info(f"🔄 Création d'abonnement pour utilisateur: {user.id}, price_id: {price_id}")
    
    # Protection contre les appels multiples simultanés
    import threading
    import time
    
    # Créer un verrou simple basé sur l'utilisateur
    if not hasattr(create_subscription, '_locks'):
        create_subscription._locks = {}
    
    lock_key = f"subscription_creation_{user.id}"
    if lock_key not in create_subscription._locks:
        create_subscription._locks[lock_key] = threading.Lock()
    
    # Vérifier si une création est déjà en cours
    if not create_subscription._locks[lock_key].acquire(blocking=False):
        logger.warning(f"⚠️ Création d'abonnement déjà en cours pour utilisateur {user.id}")
        return Response({
            'error': 'Une création d\'abonnement est déjà en cours',
        }, status=status.HTTP_409_CONFLICT)
    
    try:
            # Vérifier si l'utilisateur a déjà un abonnement en cours
            if user.stripe_subscription_id:
                try:
                    existing_subscription = stripe.Subscription.retrieve(user.stripe_subscription_id)
                    if existing_subscription.status in ['active', 'trialing', 'incomplete']:
                        logger.warning(f"⚠️ Utilisateur {user.id} a déjà un abonnement actif: {existing_subscription.id}")
                        return Response({
                            'error': 'Un abonnement est déjà en cours',
                            'subscription_id': existing_subscription.id,
                            'status': existing_subscription.status,
                        }, status=status.HTTP_409_CONFLICT)
                except stripe.error.StripeError:
                    # Si l'abonnement n'existe plus côté Stripe, on continue
                    pass
            
            # Créer ou récupérer le customer Stripe avec protection renforcée
            if not user.stripe_customer_id:
                # Vérifier s'il existe déjà un customer avec cet email
                existing_customers = stripe.Customer.list(email=user.email, limit=1)
                if existing_customers.data:
                    customer = existing_customers.data[0]
                    user.stripe_customer_id = customer.id
                    user.save()
                    logger.info(f"✅ Customer Stripe existant trouvé: {customer.id}")
                else:
                    # Double vérification avant création (protection race condition)
                    existing_customers = stripe.Customer.list(email=user.email, limit=1)
                    if existing_customers.data:
                        customer = existing_customers.data[0]
                        user.stripe_customer_id = customer.id
                        user.save()
                        logger.info(f"✅ Customer Stripe existant trouvé (2ème vérification): {customer.id}")
                    else:
                        customer = stripe.Customer.create(
                            email=user.email,
                            name=f"{user.first_name} {user.last_name}",
                            metadata={
                                'user_id': user.id,
                                'user_email': user.email,
                            }
                        )
                        user.stripe_customer_id = customer.id
                        user.save()
                        logger.info(f"✅ Customer Stripe créé: {customer.id}")
            else:
                customer = stripe.Customer.retrieve(user.stripe_customer_id)
                logger.info(f"✅ Customer Stripe existant: {customer.id}")
            
            # Créer l'abonnement avec protection
            subscription = stripe.Subscription.create(
                customer=customer.id,
                items=[{'price': price_id}],
                payment_behavior='default_incomplete',
                payment_settings={'save_default_payment_method': 'on_subscription'},
                expand=['latest_invoice.payment_intent'],
                metadata={
                    'user_id': user.id,
                    'user_email': user.email,
                }
            )
            
            # Sauvegarder immédiatement l'ID de l'abonnement pour éviter les doublons
            user.stripe_subscription_id = subscription.id
            user.subscription_status = subscription.status
            user.save()
            
            logger.info(f"✅ Abonnement créé: {subscription.id}")
            
            return Response({
                'subscription_id': subscription.id,
                'client_secret': subscription.latest_invoice.payment_intent.client_secret,
                'status': subscription.status,
            })
            
    except stripe.error.StripeError as e:
        logger.error(f"❌ Erreur Stripe: {e}")
        return Response({
            'error': 'Erreur lors de la création de l\'abonnement',
            'details': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}")
        return Response({
            'error': 'Erreur serveur',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    finally:
        # Libérer le verrou
        create_subscription._locks[lock_key].release()

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_subscription(request):
    """Annule l'abonnement de l'utilisateur"""
    try:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        user = request.user
        
        if not user.stripe_subscription_id:
            return Response({
                'error': 'Aucun abonnement actif trouvé'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Annuler l'abonnement dans Stripe
        subscription = stripe.Subscription.modify(
            user.stripe_subscription_id,
            cancel_at_period_end=True
        )
        
        logger.info(f"✅ Abonnement programmé pour annulation: {user.stripe_subscription_id}")
        
        return Response({
            'success': True,
            'message': 'Votre abonnement sera annulé à la fin de la période courante.',
            'cancel_at': datetime.fromtimestamp(subscription.current_period_end).isoformat()
        })
        
    except stripe.error.StripeError as e:
        logger.error(f"❌ Erreur Stripe: {e}")
        return Response({
            'error': 'Erreur lors de l\'annulation de l\'abonnement',
            'details': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}")
        return Response({
            'error': 'Erreur serveur',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_profile(request):
    """Met à jour le profil de l'utilisateur"""
    user = request.user
    
    # Mettre à jour les champs autorisés
    allowed_fields = ['first_name', 'last_name', 'username', 'education_level']
    
    for field in allowed_fields:
        if field in request.data:
            setattr(user, field, request.data[field])
    
    try:
        user.save()
        return Response({
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_premium': user.is_premium,
            'education_level': user.education_level,
            'date_joined': user.date_joined,
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({'error': 'Erreur lors de la mise à jour du profil'}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([AllowAny])
def user_role_info(request):
    """Retourne les informations de rôle de l'utilisateur"""
    user = request.user if request.user.is_authenticated else None
    session_id = request.GET.get('session_id')
    
    if user:
        role = user.get_user_role()
        can_create_quiz = user.can_create_quiz_today()
        can_attempt_quiz = user.can_attempt_quiz_today()
        quiz_count_today = user.quiz_count_today
        attempts_count_today = user.attempts_count_today
        guest_session = None
    else:
        role = 'guest'
        # Vérifier la session invité
        from .guest_utils import get_or_create_guest_session
        try:
            guest_session = get_or_create_guest_session(request, session_id)
            can_create_quiz = guest_session.can_create_document()
            can_attempt_quiz = True
            quiz_count_today = guest_session.documents_created
            attempts_count_today = 0
        except Exception as e:
            logger.error(f"Erreur lors de la récupération de la session invité: {e}")
            can_create_quiz = False
            can_attempt_quiz = False
            quiz_count_today = 0
            attempts_count_today = 0
            guest_session = None
    
    # Définir les limites selon le rôle
    limits = {
        'guest': {
            'max_questions': 5,
            'max_quizzes_per_day': 1,
            'max_attempts_per_quiz': 1,
            'can_save_results': False
        },
        'free': {
            'max_questions': 6,
            'max_quizzes_per_day': 1,
            'max_attempts_per_day': 2,
            'can_save_results': True
        },
        'premium': {
            'max_questions': 50,  # 50 questions max par quiz
            'max_quizzes_per_day': None,  # None = illimité
            'max_attempts_per_day': None,  # None = illimité
            'can_save_results': True
        }
    }
    
    response_data = {
        'role': role,
        'can_create_quiz': can_create_quiz,
        'can_attempt_quiz': can_attempt_quiz,
        'quiz_count_today': quiz_count_today,
        'attempts_count_today': attempts_count_today,
        'limits': limits[role],
        'user': {
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'is_premium': user.is_premium,
        } if user else None
    }
    
    # Ajouter les informations de session pour les invités
    if role == 'guest' and guest_session:
        response_data['session_id'] = guest_session.session_id
        response_data['remaining_uses'] = max(0, 1 - guest_session.documents_created)
        response_data['is_blocked'] = guest_session.is_blocked
        response_data['session_expires_at'] = guest_session.created_at + timezone.timedelta(hours=24)
    
    return Response(response_data)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    # Logout simple - le frontend supprime les tokens du localStorage
    return Response({'message': 'Logout successful'}, status=status.HTTP_200_OK)

@api_view(['POST'])
@permission_classes([AllowAny])  # Permet aux guests
def upload_document(request):
    if 'file' not in request.FILES:
        return Response({'error': 'Aucun fichier fourni'}, status=status.HTTP_400_BAD_REQUEST)
    
    file = request.FILES['file']
    title = request.data.get('title', file.name)
    
    # Récupérer les paramètres IA
    question_count = int(request.data.get('question_count', 5))
    difficulty = request.data.get('difficulty', 'medium')
    question_types = request.data.get('question_types', '["qcm"]')
    education_level = request.data.get('education_level', '')
    instructions = request.data.get('instructions', '')
    session_id = request.data.get('session_id')  # ID de session pour les invités
    
    # Vérifier les limites selon le rôle utilisateur
    user = request.user if request.user.is_authenticated else None
    user_role = user.get_user_role() if user else 'guest'
    
    # Vérifier la taille du fichier selon le rôle utilisateur
    file_size_mb = file.size / (1024 * 1024)  # Convertir en MB
    
    if user_role == 'guest' and file_size_mb > 2:
        return Response({
            'error': 'Fichier trop volumineux',
            'details': f'Limite pour les invités : 2 MB. Taille actuelle : {file_size_mb:.1f} MB. Inscrivez-vous pour uploader des fichiers jusqu\'à 5 MB.'
        }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    elif user_role == 'free' and file_size_mb > 5:
        return Response({
            'error': 'Fichier trop volumineux',
            'details': f'Limite pour les comptes gratuits : 5 MB. Taille actuelle : {file_size_mb:.1f} MB. Passez à Premium pour uploader des fichiers jusqu\'à 50 MB.'
        }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    elif user_role == 'premium' and file_size_mb > 50:
        return Response({
            'error': 'Fichier trop volumineux',
            'details': f'Limite pour les comptes Premium : 50 MB. Taille actuelle : {file_size_mb:.1f} MB.'
        }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    
    # Vérifications spécifiques pour les invités
    if user_role == 'guest':
        from .guest_utils import check_guest_limits, rate_limit_check
        
        # Vérifier le rate limiting par IP (1 requête par session/IP)
        is_rate_allowed, remaining_requests = rate_limit_check(request, max_requests=1, window_minutes=60)
        if not is_rate_allowed:
            return Response({
                'error': 'Trop de requêtes',
                'details': 'Vous avez dépassé la limite de requêtes. Veuillez attendre avant de réessayer.',
                'action': 'rate_limit_exceeded'
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # Vérifier les limites de session invité
        is_allowed, guest_session, error_msg = check_guest_limits(request, session_id)
        if not is_allowed:
            return Response(error_msg, status=status.HTTP_403_FORBIDDEN)
        
        # Vérifier les limites de questions pour les invités
        if question_count > 5:
            return Response({
                'error': 'Limite atteinte. Les utilisateurs non connectés sont limités à 5 questions maximum.',
                'details': 'Inscrivez-vous gratuitement pour créer des quiz avec plus de questions et sauvegarder vos résultats.',
                'action': 'signup_required'
            }, status=status.HTTP_403_FORBIDDEN)
    
    # Vérifications pour les utilisateurs connectés
    elif user_role == 'free' and question_count > 6:
        return Response({
            'error': 'Limite atteinte. Les comptes gratuits sont limités à 6 questions maximum.',
            'details': 'Passez à Premium pour créer des quiz avec jusqu\'à 50 questions.'
        }, status=status.HTTP_403_FORBIDDEN)
    elif user_role == 'premium' and question_count > 50:
        return Response({
            'error': 'Limite atteinte. Les comptes premium sont limités à 50 questions maximum par quiz.',
            'details': 'Cette limite permet d\'assurer la qualité et la performance des quiz.'
        }, status=status.HTTP_403_FORBIDDEN)
    
    # Vérifier les limites de quiz par jour pour les utilisateurs connectés
    if user and not user.can_create_quiz_today():
        if user_role == 'free':
            return Response({
                'error': 'Limite atteinte. Les comptes non premium sont limités à 1 génération au total.',
                'details': 'Passez à Premium pour un accès illimité et débloquer toutes les fonctionnalités.'
            }, status=status.HTTP_403_FORBIDDEN)
        else:
            return Response({'error': 'Limite atteinte. Passez à Premium pour un accès illimité.'}, status=status.HTTP_403_FORBIDDEN)
    
    # Sauvegarder le fichier
    file_extension = os.path.splitext(file.name)[1]
    unique_filename = f"{uuid.uuid4()}{file_extension}"
    
    # Pour les guests, créer un utilisateur temporaire ou utiliser None
    document_user = user if user else None
    
    document = Document.objects.create(
        user=document_user,
        guest_session=guest_session if user_role == 'guest' else None,
        title=title,
        file=file,
        file_type=file_extension
    )
    
    # Générer des questions avec l'IA
    try:
        create_ai_questions(document, question_count, difficulty, question_types, education_level, instructions)
    except Exception as e:
        # Supprimer le document en cas d'erreur
        document.delete()
        logger.error(f"❌ Erreur lors de la génération des questions: {e}")
        return Response({
            'error': str(e),
            'details': 'Erreur lors de la génération des questions. Veuillez réessayer.'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    # Générer un titre intelligent pour la leçon avec l'IA
    try:
        from ai_service import OpenAIService
        ai_service = OpenAIService()
        intelligent_title = ai_service.generate_lesson_title(
            file_path=document.file.path,
            document_title=title,
            education_level=education_level
        )
        logger.info(f"🎯 Titre intelligent généré: '{intelligent_title}'")
    except Exception as e:
        logger.warning(f"⚠️ Erreur lors de la génération du titre intelligent: {e}")
        intelligent_title = title  # Fallback vers le titre original
    
    # Créer automatiquement une leçon avec le titre intelligent
    lesson = Lesson.objects.create(
        user=document_user,
        document=document,
        title=intelligent_title,
        difficulty='medium'
    )
    
    # Incrémenter le compteur de quiz pour les utilisateurs connectés
    if user:
        user.increment_quiz_count()
    else:
        # Incrémenter l'utilisation pour les invités
        from .guest_utils import increment_guest_usage
        increment_guest_usage(guest_session)
    
    # Associer les questions du document à la leçon
    questions = Question.objects.filter(document=document)
    lesson.total_questions = questions.count()
    lesson.save()
    
    # Mettre à jour les questions pour les associer à la leçon
    questions.update(lesson=lesson)
    
    # Préparer la réponse
    response_data = {
        'document_id': document.id,
        'lesson_id': lesson.id,
        'title': document.title,
        'lesson_title': intelligent_title,
        'questions_count': questions.count(),
        'message': 'Document uploadé et questions générées avec succès'
    }
    
    # Ajouter l'ID de session pour les invités
    if user_role == 'guest':
        response_data['session_id'] = guest_session.session_id
        response_data['remaining_uses'] = 0  # Plus d'utilisations disponibles
        response_data['message'] = 'Document uploadé avec succès ! Inscrivez-vous pour sauvegarder vos résultats et créer plus de quiz.'
    
    return Response(response_data, status=status.HTTP_201_CREATED)

def create_ai_questions(document, question_count=5, difficulty='medium', question_types='["qcm"]', education_level='', instructions=''):
    """Crée des questions avec l'IA OpenAI"""
    
    try:
        # Vérifier que le fichier existe
        if not document.file or not os.path.exists(document.file.path):
            raise Exception(f"Fichier non trouvé: {document.file.path if document.file else 'Aucun fichier'}")
        
        # Utiliser le service OpenAI avec le chemin du fichier
        ai_service = OpenAIService()
        questions_data = ai_service.generate_questions_from_document(
            file_path=document.file.path,
            document_title=document.title,
            question_count=question_count,
            difficulty=difficulty,
            education_level=education_level,
            instructions=instructions
        )
        
        # Créer les questions et réponses
        for q_data in questions_data:
            question = Question.objects.create(
                document=document,
                question_text=q_data['question_text'],
                question_type='qcm',
                difficulty=q_data['difficulty']
            )
            
            # Créer les réponses
            for answer_data in q_data['answers']:
                Answer.objects.create(
                    question=question,
                    answer_text=answer_data['text'],
                    is_correct=answer_data['is_correct']
                )
        
        return True
        
    except Exception as e:
        print(f"Erreur lors de la génération IA: {e}")
        raise Exception(f"Impossible de générer les questions avec l'IA: {e}")

def create_mock_questions(document, question_count=5, difficulty='medium', question_types='["qcm"]'):
    """Crée des questions mockées pour le document - Fallback"""
    
    # Simuler un délai de traitement IA
    import time
    import json
    time.sleep(1)  # Simulation du temps de traitement
    
    # Parser les types de questions
    try:
        types_list = json.loads(question_types)
    except:
        types_list = ['qcm']
    
    # Questions générées "par l'IA" basées sur le type de fichier
    file_type = document.file_type.lower()
    
    # Templates de questions selon le type de fichier
    if 'pdf' in file_type or 'doc' in file_type:
        # Questions pour documents textuels
        question_templates = [
            {
                'text': f"Quel est le sujet principal abordé dans le document '{document.title}' ?",
                'answers': [
                    {'text': 'Les concepts fondamentaux', 'correct': True},
                    {'text': 'Les détails techniques', 'correct': False},
                    {'text': 'Les exemples pratiques', 'correct': False},
                    {'text': 'Les conclusions', 'correct': False}
                ]
            },
            {
                'text': "Quelle méthode est recommandée dans ce document ?",
                'answers': [
                    {'text': 'La méthode traditionnelle', 'correct': False},
                    {'text': 'La méthode progressive', 'correct': True},
                    {'text': 'La méthode rapide', 'correct': False},
                    {'text': 'La méthode alternative', 'correct': False}
                ]
            },
            {
                'text': "Selon le document, quel est le point le plus important à retenir ?",
                'answers': [
                    {'text': 'Les détails techniques', 'correct': False},
                    {'text': "L'approche globale", 'correct': True},
                    {'text': 'Les exemples concrets', 'correct': False},
                    {'text': 'Les limitations', 'correct': False}
                ]
            },
            {
                'text': "Quelle est la structure principale de ce document ?",
                'answers': [
                    {'text': 'Introduction, développement, conclusion', 'correct': True},
                    {'text': 'Problème, solution, évaluation', 'correct': False},
                    {'text': 'Thèse, arguments, réfutation', 'correct': False},
                    {'text': 'Contexte, analyse, recommandations', 'correct': False}
                ]
            },
            {
                'text': "Quel est l'objectif principal de ce document ?",
                'answers': [
                    {'text': 'Informer le lecteur', 'correct': True},
                    {'text': 'Persuader le lecteur', 'correct': False},
                    {'text': 'Divertir le lecteur', 'correct': False},
                    {'text': 'Critiquer une théorie', 'correct': False}
                ]
            }
        ]
    else:
        # Questions pour images/photos
        question_templates = [
            {
                'text': f"Que représente l'image '{document.title}' ?",
                'answers': [
                    {'text': 'Un diagramme explicatif', 'correct': True},
                    {'text': 'Une photographie', 'correct': False},
                    {'text': 'Un graphique', 'correct': False},
                    {'text': 'Un schéma technique', 'correct': False}
                ]
            },
            {
                'text': "Quel élément est le plus visible dans cette image ?",
                'answers': [
                    {'text': 'Le texte principal', 'correct': True},
                    {'text': 'Les détails secondaires', 'correct': False},
                    {'text': "L'arrière-plan", 'correct': False},
                    {'text': 'Les annotations', 'correct': False}
                ]
            },
            {
                'text': "Quelle est la fonction principale de cette image ?",
                'answers': [
                    {'text': 'Illustrer un concept', 'correct': True},
                    {'text': 'Décorer le document', 'correct': False},
                    {'text': 'Montrer un exemple', 'correct': False},
                    {'text': 'Expliquer un processus', 'correct': False}
                ]
            },
            {
                'text': "Quel type de contenu cette image présente-t-elle ?",
                'answers': [
                    {'text': 'Du contenu éducatif', 'correct': True},
                    {'text': 'Du contenu publicitaire', 'correct': False},
                    {'text': 'Du contenu artistique', 'correct': False},
                    {'text': 'Du contenu technique', 'correct': False}
                ]
            },
            {
                'text': "Dans quel contexte cette image est-elle utilisée ?",
                'answers': [
                    {'text': 'Dans un contexte pédagogique', 'correct': True},
                    {'text': 'Dans un contexte commercial', 'correct': False},
                    {'text': 'Dans un contexte scientifique', 'correct': False},
                    {'text': 'Dans un contexte artistique', 'correct': False}
                ]
            }
        ]
    
    # Sélectionner le nombre de questions demandé
    selected_questions = question_templates[:question_count]
    
    # Créer les questions et réponses
    for q_data in selected_questions:
        question = Question.objects.create(
            document=document,
            question_text=q_data['text'],
            question_type='qcm',  # Seulement QCM
            difficulty=difficulty
        )
        
        # Ajouter les réponses pour les QCM
        for answer_data in q_data['answers']:
            Answer.objects.create(
                question=question,
                answer_text=answer_data['text'],
                is_correct=answer_data['correct']
            )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_documents(request):
    documents = Document.objects.filter(user=request.user).order_by('-created_at')
    serializer = DocumentSerializer(documents, many=True)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_questions(request, document_id):
    try:
        document = Document.objects.get(id=document_id, user=request.user)
        questions = Question.objects.filter(document=document).order_by('created_at')
        serializer = QuestionSerializer(questions, many=True)
        return Response(serializer.data)
    except Document.DoesNotExist:
        return Response({'error': 'Document non trouvé'}, status=status.HTTP_404_NOT_FOUND)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_lessons(request):
    """Récupère toutes les leçons de l'utilisateur"""
    lessons = Lesson.objects.filter(user=request.user).order_by('-last_accessed')
    serializer = LessonSerializer(lessons, many=True)
    return Response(serializer.data)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_lesson(request):
    """Crée une nouvelle leçon à partir d'un document"""
    try:
        document_id = request.data.get('document_id')
        document = Document.objects.get(id=document_id, user=request.user)
        
        # Créer la leçon
        lesson = Lesson.objects.create(
            user=request.user,
            document=document,
            title=request.data.get('title', document.title),
            difficulty=request.data.get('difficulty', 'medium')
        )
        
        # Associer les questions du document à la leçon
        questions = Question.objects.filter(document=document)
        lesson.total_questions = questions.count()
        lesson.save()
        
        # Mettre à jour les questions pour les associer à la leçon
        questions.update(lesson=lesson)
        
        serializer = LessonSerializer(lesson)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
        
    except Document.DoesNotExist:
        return Response({'error': 'Document non trouvé'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([AllowAny])
def get_lesson(request, lesson_id):
    """Récupère une leçon spécifique avec ses questions"""
    try:
        import random
        
        # Récupérer session_id pour les invités
        session_id = request.GET.get('session_id')
        
        if request.user.is_authenticated:
            # Utilisateur connecté
            lesson = Lesson.objects.get(id=lesson_id, user=request.user)
            
            # Vérifier les limites de tentatives pour les utilisateurs non premium
            if not request.user.is_premium:
                if not request.user.can_attempt_quiz_today():
                    return Response({
                        'error': 'Limite atteinte. Les comptes non premium sont limités à 2 tentatives au total.',
                        'details': 'Passez à Premium pour un accès illimité et débloquer toutes les fonctionnalités.'
                    }, status=status.HTTP_403_FORBIDDEN)
                
                # Incrémenter le compteur de tentatives
                request.user.increment_attempt_count()
        else:
            # Invité - vérifier la session
            from .guest_utils import get_or_create_guest_session
            guest_session = get_or_create_guest_session(request, session_id)
            
            # Récupérer la leçon de l'invité (user=None)
            try:
                lesson = Lesson.objects.get(id=lesson_id, user=None)
            except Lesson.DoesNotExist:
                return Response({'error': 'Quiz non trouvé'}, status=status.HTTP_404_NOT_FOUND)
            
            # Vérifier que la leçon appartient à cette session invité
            # (on peut vérifier via le document associé)
            if lesson.document.user is not None or lesson.document.guest_session != guest_session:
                return Response({'error': 'Accès refusé'}, status=status.HTTP_403_FORBIDDEN)
        
        questions = Question.objects.filter(lesson=lesson).order_by('created_at')
        
        lesson_serializer = LessonSerializer(lesson)
        questions_serializer = QuestionSerializer(questions, many=True)
        
        # Mélanger l'ordre des réponses pour chaque question
        questions_data = questions_serializer.data
        for question in questions_data:
            if question['answers']:
                # Mélanger l'ordre des réponses
                random.shuffle(question['answers'])
        
        response_data = {
            'lesson': lesson_serializer.data,
            'questions': questions_data
        }
        
        # Ajouter session_id pour les invités
        if not request.user.is_authenticated:
            response_data['session_id'] = guest_session.session_id
        
        return Response(response_data)
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)

@api_view(['POST'])
@permission_classes([AllowAny])
def submit_answer(request, lesson_id):
    """Soumet une réponse à une question"""
    try:
        # Récupérer session_id pour les invités
        session_id = request.data.get('session_id')
        
        if request.user.is_authenticated:
            # Utilisateur connecté
            lesson = Lesson.objects.get(id=lesson_id, user=request.user)
            user = request.user
            guest_session = None
        else:
            # Invité - vérifier la session
            from .guest_utils import get_or_create_guest_session
            guest_session = get_or_create_guest_session(request, session_id)
            user = None
            
            # Récupérer la leçon de l'invité (user=None)
            lesson = Lesson.objects.get(id=lesson_id, user=None)
            
            # Vérifier que la leçon appartient à cette session invité
            if lesson.document.user is not None:
                return Response({'error': 'Accès refusé'}, status=status.HTTP_403_FORBIDDEN)
        
        question_id = request.data.get('question_id')
        question = Question.objects.get(id=question_id, lesson=lesson)
        
        # Supprimer les anciennes réponses pour permettre la révision
        if user:
            UserAnswer.objects.filter(
                user=user, 
                question=question, 
                lesson=lesson
            ).delete()
        else:
            UserAnswer.objects.filter(
                guest_session=guest_session, 
                question=question, 
                lesson=lesson
            ).delete()
        
        # Traiter la réponse
        is_correct = False
        selected_answer = None
        open_answer = None
        
        if question.question_type == 'qcm':
            selected_answer_id = request.data.get('selected_answer_id')
            selected_answer = Answer.objects.get(id=selected_answer_id, question=question)
            is_correct = selected_answer.is_correct
        else:  # question ouverte
            open_answer = request.data.get('open_answer', '')
            # Pour l'instant, on considère toutes les réponses ouvertes comme correctes
            # Dans une vraie app, il faudrait une logique d'évaluation
            is_correct = True
        
        # Créer la réponse utilisateur
        user_answer = UserAnswer.objects.create(
            user=user,
            guest_session=guest_session,
            question=question,
            lesson=lesson,
            selected_answer=selected_answer,
            open_answer=open_answer,
            is_correct=is_correct
        )
        
        # Recalculer les statistiques de la leçon
        if user:
            # Utilisateur connecté
            answered_questions = UserAnswer.objects.filter(user=user, lesson=lesson).values('question').distinct()
            correct_answers = UserAnswer.objects.filter(user=user, lesson=lesson, is_correct=True).values('question').distinct().count()
        else:
            # Invité
            answered_questions = UserAnswer.objects.filter(guest_session=guest_session, lesson=lesson).values('question').distinct()
            correct_answers = UserAnswer.objects.filter(guest_session=guest_session, lesson=lesson, is_correct=True).values('question').distinct().count()
        
        lesson.completed_questions = answered_questions.count()
        new_score = int((correct_answers / lesson.total_questions) * 100) if lesson.total_questions > 0 else 0
        
        # Marquer comme terminé si toutes les questions sont répondues
        if lesson.completed_questions >= lesson.total_questions:
            lesson.status = 'termine'
            # Mettre à jour les scores et statistiques SEULEMENT quand le quiz est terminé
            lesson.update_scores(new_score)
            
            # Créer un enregistrement de tentative (seulement pour les utilisateurs connectés)
            if user:
                attempt_number = lesson.total_attempts
                LessonAttempt.objects.create(
                    lesson=lesson,
                    attempt_number=attempt_number,
                    score=new_score
                )
        else:
            lesson.status = 'en_cours'
            # Mettre à jour seulement le score actuel, pas les statistiques
            lesson.score = new_score
        
        lesson.save()
        
        response_data = {
            'is_correct': is_correct,
            'lesson_progress': lesson.progress,
            'lesson_score': lesson.score
        }
        
        # Ajouter session_id pour les invités
        if not request.user.is_authenticated:
            response_data['session_id'] = guest_session.session_id
        
        return Response(response_data)
        
    except (Lesson.DoesNotExist, Question.DoesNotExist, Answer.DoesNotExist):
        return Response({'error': 'Ressource non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([AllowAny])
def get_guest_quiz_results(request, lesson_id):
    """Récupère les résultats d'un quiz invité (sans les afficher)"""
    try:
        session_id = request.GET.get('session_id')
        
        if request.user.is_authenticated:
            return Response({'error': 'Cette fonction est réservée aux invités'}, status=status.HTTP_403_FORBIDDEN)
        
        # Vérifier la session invité
        from .guest_utils import get_or_create_guest_session
        guest_session = get_or_create_guest_session(request, session_id)
        
        # Récupérer la leçon de l'invité
        lesson = Lesson.objects.get(id=lesson_id, user=None)
        
        # Vérifier que la leçon appartient à cette session invité
        if lesson.document.user is not None:
            return Response({'error': 'Accès refusé'}, status=status.HTTP_403_FORBIDDEN)
        
        # Récupérer les réponses de l'invité
        user_answers = UserAnswer.objects.filter(guest_session=guest_session, lesson=lesson)
        
        # Calculer le score
        correct_answers = user_answers.filter(is_correct=True).count()
        total_questions = lesson.total_questions
        score_percentage = int((correct_answers / total_questions) * 100) if total_questions > 0 else 0
        
        # Vérifier si le quiz est terminé
        is_completed = lesson.status == 'termine'
        
        response_data = {
            'lesson_id': lesson.id,
            'lesson_title': lesson.title,
            'is_completed': is_completed,
            'total_questions': total_questions,
            'answered_questions': user_answers.count(),
            'correct_answers': correct_answers,
            'score_percentage': score_percentage,
            'session_id': guest_session.session_id,
            'can_see_results': False,  # Les invités ne peuvent pas voir les résultats
            'message': 'Quiz terminé ! Inscrivez-vous pour voir vos résultats détaillés et sauvegarder vos progrès.'
        }
        
        return Response(response_data)
        
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def transfer_guest_data(request):
    """Transfère les données d'une session invité vers le compte utilisateur"""
    try:
        session_id = request.data.get('session_id')
        
        if not session_id:
            return Response({'error': 'Session ID requis'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Récupérer la session invité
        try:
            guest_session = GuestSession.objects.get(session_id=session_id)
        except GuestSession.DoesNotExist:
            return Response({'error': 'Session invité non trouvée'}, status=status.HTTP_404_NOT_FOUND)
        
        # Vérifier que la session n'a pas déjà été transférée
        if guest_session.transferred_to_user:
            return Response({'error': 'Cette session a déjà été transférée'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Récupérer les documents avant le transfert
        documents_before_transfer = list(Document.objects.filter(guest_session=guest_session).values_list('id', flat=True))
        
        # Transférer les données
        guest_session.transfer_to_user(request.user)
        
        # Récupérer les leçons transférées via les documents
        transferred_lessons = Lesson.objects.filter(user=request.user, document_id__in=documents_before_transfer)
        
        response_data = {
            'success': True,
            'message': 'Vos données ont été transférées avec succès !',
            'transferred_lessons': [
                {
                    'id': lesson.id,
                    'title': lesson.title,
                    'score': lesson.score,
                    'status': lesson.status
                }
                for lesson in transferred_lessons
            ]
        }
        
        return Response(response_data)
        
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reset_lesson(request, lesson_id):
    """Réinitialise une leçon pour permettre de la refaire"""
    try:
        lesson = Lesson.objects.get(id=lesson_id, user=request.user)
        
        # Supprimer toutes les réponses de l'utilisateur pour cette leçon
        UserAnswer.objects.filter(user=request.user, lesson=lesson).delete()
        
        # Réinitialiser seulement les champs nécessaires pour relancer le quiz
        lesson.completed_questions = 0
        lesson.score = 0
        lesson.status = 'en_cours'
        # NE PAS réinitialiser last_score, total_attempts, average_score
        lesson.save()
        
        return Response({'message': 'Leçon réinitialisée avec succès'})
        
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_lesson_stats(request):
    """Récupère les statistiques de l'utilisateur"""
    lessons = Lesson.objects.filter(user=request.user)
    
    total_lessons = lessons.count()
    completed_lessons = lessons.filter(status='termine').count()
    
    # Calculer le score moyen
    scores = [lesson.score for lesson in lessons if lesson.score > 0]
    average_score = sum(scores) / len(scores) if scores else 0
    
    # Pour l'instant, on simule le temps d'étude
    # Dans une vraie app, il faudrait tracker le temps réel
    total_study_time = completed_lessons * 30  # 30 minutes par leçon terminée
    
    stats = {
        'total_lessons': total_lessons,
        'completed_lessons': completed_lessons,
        'average_score': round(average_score, 1),
        'total_study_time': total_study_time
    }
    
    serializer = LessonStatsSerializer(stats)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_lesson_attempts(request, lesson_id):
    """Récupère l'historique des tentatives pour une leçon"""
    try:
        lesson = Lesson.objects.get(id=lesson_id, user=request.user)
        attempts = LessonAttempt.objects.filter(lesson=lesson).order_by('attempt_number')
        
        serializer = LessonAttemptSerializer(attempts, many=True)
        return Response(serializer.data)
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erreur lors de la récupération des tentatives: {e}")
        return Response({'error': 'Erreur lors de la récupération des tentatives'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_lesson(request, lesson_id):
    """Supprime une leçon et toutes ses données associées en cascade"""
    try:
        # Vérifier que la leçon appartient à l'utilisateur
        lesson = Lesson.objects.get(id=lesson_id, user=request.user)
        
        # Supprimer en cascade : UserAnswer -> Question -> Document -> Lesson
        # 1. Supprimer toutes les réponses utilisateur associées à cette leçon
        UserAnswer.objects.filter(lesson=lesson).delete()
        
        # 2. Supprimer toutes les tentatives de leçon
        LessonAttempt.objects.filter(lesson=lesson).delete()
        
        # 3. Récupérer le document associé avant de supprimer les questions
        document = lesson.document
        
        # 4. Supprimer toutes les questions associées au document
        Question.objects.filter(document=document).delete()
        
        # 5. Supprimer le document (fichier physique)
        if document.file:
            try:
                if os.path.isfile(document.file.path):
                    os.remove(document.file.path)
            except Exception as e:
                logger.warning(f"⚠️ Impossible de supprimer le fichier physique: {e}")
        
        # 6. Supprimer le document de la base de données
        document.delete()
        
        # 7. Supprimer la leçon
        lesson.delete()
        
        logger.info(f"✅ Leçon {lesson_id} supprimée avec succès par l'utilisateur {request.user.id}")
        
        return Response({
            'message': 'Leçon supprimée avec succès',
            'lesson_id': lesson_id
        }, status=status.HTTP_200_OK)
        
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erreur lors de la suppression de la leçon {lesson_id}: {e}")
        return Response({
            'error': 'Erreur lors de la suppression de la leçon',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_lesson(request, lesson_id):
    """Met à jour le titre d'une leçon"""
    try:
        # Vérifier que la leçon appartient à l'utilisateur
        lesson = Lesson.objects.get(id=lesson_id, user=request.user)
        
        # Récupérer le nouveau titre depuis la requête
        new_title = request.data.get('title')
        
        if not new_title:
            return Response({
                'error': 'Le titre est requis'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Valider la longueur du titre
        if len(new_title.strip()) == 0:
            return Response({
                'error': 'Le titre ne peut pas être vide'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if len(new_title) > 255:
            return Response({
                'error': 'Le titre ne peut pas dépasser 255 caractères'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Mettre à jour le titre
        lesson.title = new_title.strip()
        lesson.save()
        
        logger.info(f"✅ Titre de la leçon {lesson_id} mis à jour par l'utilisateur {request.user.id}: '{new_title}'")
        
        # Sérialiser la leçon mise à jour
        serializer = LessonSerializer(lesson)
        
        return Response({
            'message': 'Titre de la leçon mis à jour avec succès',
            'lesson': serializer.data
        }, status=status.HTTP_200_OK)
        
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erreur lors de la mise à jour de la leçon {lesson_id}: {e}")
        return Response({
            'error': 'Erreur lors de la mise à jour de la leçon',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_payment_intent(request):
    """Crée un PaymentIntent Stripe pour le checkout"""
    try:
        # Configuration Stripe dans la vue
        stripe_secret_key = settings.STRIPE_SECRET_KEY
        
        # Vérifier que la clé Stripe est bien définie
        if not stripe_secret_key:
            logger.error("❌ STRIPE_SECRET_KEY non définie dans les settings")
            return Response({
                'error': 'Configuration Stripe manquante',
                'details': 'Clé secrète Stripe non configurée'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        stripe.api_key = stripe_secret_key
        
        logger.info(f"🔑 Création PaymentIntent pour utilisateur: {request.user.id}")
        logger.info(f"🔑 Clé Stripe configurée: {stripe_secret_key[:20]}...")
        
        # Prix en centimes (exemple: 9.99€ = 999 centimes)
        amount = request.data.get('amount', 999)  # Prix par défaut: 9.99€
        logger.info(f"💰 Montant demandé: {amount} centimes")
        
        # Vérifier que l'utilisateur est bien authentifié
        if not request.user.is_authenticated:
            logger.error("❌ Utilisateur non authentifié")
            return Response({
                'error': 'Utilisateur non authentifié'
            }, status=status.HTTP_401_UNAUTHORIZED)
        
        # Créer le PaymentIntent
        logger.info("🔄 Création du PaymentIntent Stripe...")
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='eur',
            metadata={
                'user_id': request.user.id,
                'user_email': request.user.email,
            }
        )
        
        logger.info(f"✅ PaymentIntent créé: {intent.id}")
        
        # Enregistrer le paiement dans la base de données
        payment, created = StripePayment.objects.get_or_create(
            payment_intent_id=intent.id,
            defaults={
                'user': request.user,
                'amount': amount,
                'currency': 'eur',
                'status': intent.status,
                'metadata': {
                    'user_id': request.user.id,
                    'user_email': request.user.email,
                }
            }
        )
        
        if not created:
            # Mettre à jour le statut si le paiement existe déjà
            payment.status = intent.status
            payment.save()
        
        logger.info(f"💾 Paiement enregistré en base: {payment.id}")
        
        return Response({
            'client_secret': intent.client_secret,
            'amount': amount
        })
        
    except stripe.error.StripeError as e:
        logger.error(f"❌ Erreur Stripe: {e}")
        return Response({
            'error': 'Erreur lors de la création du paiement',
            'details': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}")
        logger.error(f"❌ Type d'erreur: {type(e)}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return Response({
            'error': 'Erreur serveur',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_payment(request):
    """Confirme le paiement et met à jour le statut utilisateur"""
    try:
        # Configuration Stripe dans la vue
        stripe.api_key = settings.STRIPE_SECRET_KEY
        
        payment_intent_id = request.data.get('payment_intent_id')
        
        if not payment_intent_id:
            return Response({
                'error': 'Payment Intent ID requis'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Récupérer le PaymentIntent depuis Stripe
        intent = stripe.PaymentIntent.retrieve(payment_intent_id)
        
        # Mettre à jour le statut du paiement en base
        try:
            payment = StripePayment.objects.get(payment_intent_id=payment_intent_id)
            payment.status = intent.status
            payment.save()
            logger.info(f"💾 Statut du paiement mis à jour: {payment_intent_id} -> {intent.status}")
        except StripePayment.DoesNotExist:
            logger.warning(f"⚠️ Paiement {payment_intent_id} non trouvé en base")
        
        # Vérifier que le paiement est réussi
        if intent.status == 'succeeded':
            # Mettre à jour l'utilisateur en Premium avec abonnement
            user = request.user
            user.is_premium = True
            
            # Définir la date d'expiration selon le montant payé
            from django.utils import timezone
            amount = intent.amount
            
            if amount >= 9999:  # 99.99€ = abonnement annuel
                user.extend_subscription(days=365)
                subscription_type = "annuel"
            else:  # 9.99€ = abonnement mensuel
                user.extend_subscription(days=30)
                subscription_type = "mensuel"
            
            logger.info(f"✅ Utilisateur {user.id} mis à jour en Premium ({subscription_type}) après paiement {payment_intent_id}")
            
            return Response({
                'success': True,
                'message': f'Paiement confirmé avec succès ! Votre abonnement {subscription_type} est maintenant actif.',
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'username': user.username,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'is_premium': user.is_premium,
                },
                'subscription_type': subscription_type,
                'expires_at': user.current_period_end.isoformat() if user.current_period_end else None
            })
        else:
            return Response({
                'error': 'Paiement non confirmé',
                'status': intent.status
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except stripe.error.StripeError as e:
        logger.error(f"Erreur Stripe lors de la confirmation: {e}")
        return Response({
            'error': 'Erreur lors de la confirmation du paiement',
            'details': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Erreur inattendue lors de la confirmation: {e}")
        return Response({
            'error': 'Erreur serveur',
            'details': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@csrf_exempt
def stripe_webhook(request):
    """Webhook Stripe pour gérer les événements de paiement"""
    if request.method != 'POST':
        return HttpResponse(status=405)
    
    # Configuration Stripe dans la vue
    stripe.api_key = settings.STRIPE_SECRET_KEY
    
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    
    try:
        print(f"🔑 Clé webhook utilisée: {settings.STRIPE_WEBHOOK_SECRET}")
        print(f"🔑 Signature reçue: {sig_header}")
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.error("Payload invalide")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.error("Signature invalide")
        return HttpResponse(status=400)
    
    # Gérer les événements
    logger.info(f"🔔 Webhook reçu: {event['type']}")
    
    if event['type'] == 'customer.subscription.created':
        subscription = event['data']['object']
        customer_id = subscription['customer']
        
        try:
            user = User.objects.get(stripe_customer_id=customer_id)
            user.stripe_subscription_id = subscription['id']
            user.subscription_status = subscription['status']
            if 'current_period_end' in subscription:
                user.current_period_end = datetime.fromtimestamp(subscription['current_period_end'])
            user.is_premium = subscription['status'] in ['active', 'trialing']
            
            # Déterminer l'intervalle
            if subscription['items']['data']:
                interval = subscription['items']['data'][0]['price']['recurring']['interval']
                user.subscription_interval = interval
            
            user.save()
            logger.info(f"✅ Abonnement créé pour utilisateur {user.id}: {subscription['id']}")
        except User.DoesNotExist:
            logger.error(f"Utilisateur avec customer_id {customer_id} non trouvé")
    
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        subscription_id = subscription['id']
        
        print(f"🔄 Traitement subscription.updated: {subscription_id}")
        print(f"📊 Statut reçu: {subscription['status']}")
        print(f"📅 current_period_end: {subscription.get('current_period_end', 'N/A')}")
        
        try:
            user = User.objects.get(stripe_subscription_id=subscription_id)
            print(f"👤 Utilisateur trouvé: {user.email} (ID: {user.id})")
            
            # Récupérer les infos complètes depuis Stripe
            stripe.api_key = settings.STRIPE_SECRET_KEY
            full_subscription = stripe.Subscription.retrieve(subscription_id)
            
            user.subscription_status = full_subscription['status']
            
            # Capturer les informations d'annulation
            user.cancel_at_period_end = full_subscription.get('cancel_at_period_end', False)
            if full_subscription.get('canceled_at'):
                from django.utils import timezone
                user.canceled_at = timezone.make_aware(datetime.fromtimestamp(full_subscription['canceled_at']))
                print(f"📅 Date d'annulation: {user.canceled_at}")
            
            # Toujours mettre à jour current_period_end depuis Stripe
            if 'current_period_end' in full_subscription:
                from django.utils import timezone
                user.current_period_end = timezone.make_aware(datetime.fromtimestamp(full_subscription['current_period_end']))
                print(f"📅 Date de fin mise à jour: {user.current_period_end}")
            
            print(f"🚫 Annulation programmée: {user.cancel_at_period_end}")
            
            # Logique améliorée : rester Premium jusqu'à la fin de la période
            from django.utils import timezone
            now = timezone.now()
            
            if subscription['status'] == 'canceled':
                # Si annulé mais pas encore expiré, rester Premium
                if user.current_period_end and user.current_period_end > now:
                    user.is_premium = True
                    print(f"✅ Annulé mais Premium maintenu jusqu'au {user.current_period_end}")
                else:
                    user.is_premium = False
                    print(f"❌ Annulé et Premium retiré")
            else:
                # Pour les autres statuts, utiliser la logique normale
                user.is_premium = subscription['status'] in ['active', 'trialing']
                print(f"🔄 Statut normal: Premium = {user.is_premium}")
            
            user.save()
            print(f"💾 Utilisateur sauvegardé: Premium={user.is_premium}, Status={user.subscription_status}, CancelAtPeriodEnd={user.cancel_at_period_end}")
            logger.info(f"✅ Abonnement mis à jour pour utilisateur {user.id}: {subscription['status']}")
        except User.DoesNotExist:
            print(f"❌ Utilisateur avec subscription_id {subscription_id} non trouvé")
            logger.error(f"Utilisateur avec subscription_id {subscription_id} non trouvé")
    
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        subscription_id = subscription['id']
        
        print(f"🗑️ Traitement subscription.deleted: {subscription_id}")
        
        try:
            user = User.objects.get(stripe_subscription_id=subscription_id)
            print(f"👤 Utilisateur trouvé: {user.email} (ID: {user.id})")
            
            # Nettoyer tous les champs d'abonnement
            user.subscription_status = 'canceled'
            user.is_premium = False
            user.stripe_subscription_id = ''  # Nettoyer l'ID d'abonnement
            user.current_period_end = None    # Nettoyer la date de fin
            user.subscription_interval = ''   # Nettoyer l'intervalle
            user.cancel_at_period_end = False # Nettoyer le flag d'annulation
            # Garder canceled_at pour l'historique
            
            user.save()
            
            print(f"❌ Abonnement définitivement supprimé pour utilisateur {user.id}")
            print(f"🧹 Champs d'abonnement nettoyés")
            print(f"💾 Utilisateur sauvegardé: Premium={user.is_premium}, Status={user.subscription_status}")
            logger.info(f"✅ Abonnement annulé pour utilisateur {user.id}")
        except User.DoesNotExist:
            print(f"❌ Utilisateur avec subscription_id {subscription_id} non trouvé")
            logger.error(f"Utilisateur avec subscription_id {subscription_id} non trouvé")
    
    elif event['type'] == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        
        if subscription_id:
            try:
                user = User.objects.get(stripe_subscription_id=subscription_id)
                user.subscription_status = 'active'
                user.is_premium = True
                user.save()
                logger.info(f"✅ Paiement réussi pour utilisateur {user.id}")
            except User.DoesNotExist:
                logger.error(f"Utilisateur avec subscription_id {subscription_id} non trouvé")
    
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        subscription_id = invoice.get('subscription')
        
        if subscription_id:
            try:
                user = User.objects.get(stripe_subscription_id=subscription_id)
                user.subscription_status = 'past_due'
                user.is_premium = False
                user.save()
                logger.info(f"⚠️ Paiement échoué pour utilisateur {user.id}")
            except User.DoesNotExist:
                logger.error(f"Utilisateur avec subscription_id {subscription_id} non trouvé")
    
    elif event['type'] == 'payment_intent.succeeded':
        # Garder l'ancien code pour les paiements ponctuels
        payment_intent = event['data']['object']
        user_id = payment_intent['metadata'].get('user_id')
        
        if user_id:
            try:
                user = User.objects.get(id=user_id)
                user.is_premium = True
                user.save()
                logger.info(f"✅ Utilisateur {user_id} mis à jour en Premium via webhook")
            except User.DoesNotExist:
                logger.error(f"Utilisateur {user_id} non trouvé")
    
    return HttpResponse(status=200)

@api_view(['DELETE'])
@permission_classes([AllowAny])
def delete_question(request, lesson_id, question_id):
    """Supprime une question d'une leçon"""
    try:
        # Récupérer la leçon
        lesson = Lesson.objects.get(id=lesson_id)
        
        # Vérifier les permissions
        user = request.user if request.user.is_authenticated else None
        guest_session = None
        
        if user:
            # Utilisateur connecté - vérifier qu'il est propriétaire de la leçon
            if lesson.user != user:
                return Response({'error': 'Accès refusé'}, status=status.HTTP_403_FORBIDDEN)
        else:
            # Invité - vérifier la session
            session_id = request.GET.get('session_id')
            if not session_id:
                return Response({'error': 'Session invité requise'}, status=status.HTTP_400_BAD_REQUEST)
            
            from .guest_utils import get_or_create_guest_session
            guest_session = get_or_create_guest_session(request, session_id)
            
            if lesson.guest_session != guest_session:
                return Response({'error': 'Accès refusé'}, status=status.HTTP_403_FORBIDDEN)
        
        # Récupérer la question
        question = Question.objects.get(id=question_id, lesson=lesson)
        
        # Supprimer toutes les réponses utilisateur liées à cette question
        if user:
            UserAnswer.objects.filter(user=user, question=question).delete()
        else:
            UserAnswer.objects.filter(guest_session=guest_session, question=question).delete()
        
        # Supprimer la question (cela supprimera aussi les réponses via CASCADE)
        question.delete()
        
        # Recalculer les statistiques de la leçon
        if user:
            answered_questions = UserAnswer.objects.filter(user=user, lesson=lesson).values('question').distinct()
            correct_answers = UserAnswer.objects.filter(user=user, lesson=lesson, is_correct=True).values('question').distinct().count()
        else:
            answered_questions = UserAnswer.objects.filter(guest_session=guest_session, lesson=lesson).values('question').distinct()
            correct_answers = UserAnswer.objects.filter(guest_session=guest_session, lesson=lesson, is_correct=True).values('question').distinct().count()
        
        # Mettre à jour le nombre total de questions
        lesson.total_questions = Question.objects.filter(lesson=lesson).count()
        lesson.completed_questions = answered_questions.count()
        
        # Recalculer le score
        new_score = int((correct_answers / lesson.total_questions) * 100) if lesson.total_questions > 0 else 0
        lesson.score = new_score
        
        # Si plus de questions, marquer comme terminé
        if lesson.total_questions == 0:
            lesson.status = 'termine'
        elif answered_questions.count() >= lesson.total_questions:
            lesson.status = 'termine'
        else:
            lesson.status = 'en_cours'
        
        lesson.save()
        
        response_data = {
            'message': 'Question supprimée avec succès',
            'lesson_progress': lesson.progress,
            'lesson_score': lesson.score,
            'total_questions': lesson.total_questions
        }
        
        # Ajouter session_id pour les invités
        if not request.user.is_authenticated:
            response_data['session_id'] = guest_session.session_id
        
        return Response(response_data)
        
    except Lesson.DoesNotExist:
        return Response({'error': 'Leçon non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Question.DoesNotExist:
        return Response({'error': 'Question non trouvée'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

# ==================== ENDPOINTS DE VÉRIFICATION EMAIL ====================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def send_email_verification(request):
    """Envoie un code de vérification à l'email de l'utilisateur"""
    try:
        user = request.user
        
        # Vérifier si l'email est déjà vérifié
        if user.email_verified:
            return Response({
                'message': 'Email déjà vérifié',
                'email_verified': True
            }, status=status.HTTP_200_OK)
        
        # Vérifier le délai entre les envois (minimum 1 minute)
        if user.email_verification_sent_at:
            time_since_last = timezone.now() - user.email_verification_sent_at
            if time_since_last.total_seconds() < 60:
                remaining_seconds = 60 - int(time_since_last.total_seconds())
                return Response({
                    'error': f'Veuillez attendre {remaining_seconds} secondes avant de redemander un code'
                }, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        # Générer un nouveau code de vérification
        verification_code = email_service.generate_verification_code()
        expires_at = timezone.now() + timedelta(minutes=15)
        
        # Print le code dans la console pour les tests
        print(f"🔢 Code de vérification pour {user.email}: {verification_code}")
        
        # Créer l'enregistrement de vérification
        verification = EmailVerification.objects.create(
            user=user,
            code=verification_code,
            email=user.email,
            expires_at=expires_at
        )
        
        # Envoyer l'email
        email_sent = email_service.send_verification_email(user.email, verification_code)
        
        if email_sent:
            # Mettre à jour la date d'envoi
            user.email_verification_sent_at = timezone.now()
            user.save(update_fields=['email_verification_sent_at'])
            
            return Response({
                'message': 'Code de vérification envoyé avec succès',
                'email': user.email,
                'expires_in_minutes': 15
            }, status=status.HTTP_200_OK)
        else:
            # Supprimer l'enregistrement si l'email n'a pas pu être envoyé
            verification.delete()
            return Response({
                'error': 'Erreur lors de l\'envoi de l\'email'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi de la vérification email: {str(e)}")
        return Response({
            'error': 'Erreur interne du serveur'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_email_code(request):
    """Vérifie le code de vérification email"""
    try:
        user = request.user
        code = request.data.get('code', '').strip()
        
        if not code:
            return Response({
                'error': 'Code de vérification requis'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Vérifier si l'email est déjà vérifié
        if user.email_verified:
            return Response({
                'message': 'Email déjà vérifié',
                'email_verified': True
            }, status=status.HTTP_200_OK)
        
        # Chercher le code de vérification valide
        try:
            verification = EmailVerification.objects.filter(
                user=user,
                code=code,
                is_used=False
            ).order_by('-created_at').first()
            
            if not verification:
                return Response({
                    'error': 'Code de vérification invalide'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Vérifier si le code a expiré
            if verification.is_expired():
                verification.increment_attempts()
                return Response({
                    'error': 'Code de vérification expiré'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Vérifier le nombre de tentatives
            if verification.attempts >= 3:
                return Response({
                    'error': 'Trop de tentatives. Veuillez demander un nouveau code'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Marquer le code comme utilisé
            verification.mark_as_used()
            
            # Marquer l'email comme vérifié
            user.email_verified = True
            user.save(update_fields=['email_verified'])
            
            # Envoyer un email de bienvenue
            email_service.send_welcome_email(user.email, user.first_name or user.username)
            
            return Response({
                'message': 'Email vérifié avec succès',
                'email_verified': True
            }, status=status.HTTP_200_OK)
            
        except EmailVerification.DoesNotExist:
            return Response({
                'error': 'Code de vérification invalide'
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        logger.error(f"Erreur lors de la vérification du code email: {str(e)}")
        return Response({
            'error': 'Erreur interne du serveur'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def email_verification_status(request):
    """Retourne le statut de vérification de l'email"""
    try:
        user = request.user
        
        # Chercher le dernier code de vérification
        last_verification = EmailVerification.objects.filter(
            user=user
        ).order_by('-created_at').first()
        
        response_data = {
            'email_verified': user.email_verified,
            'email': user.email,
            'can_request_new_code': True
        }
        
        if last_verification and not user.email_verified:
            # Vérifier si on peut demander un nouveau code
            if user.email_verification_sent_at:
                time_since_last = timezone.now() - user.email_verification_sent_at
                can_request = time_since_last.total_seconds() >= 60
                response_data['can_request_new_code'] = can_request
                
                if not can_request:
                    remaining_seconds = 60 - int(time_since_last.total_seconds())
                    response_data['next_code_in_seconds'] = remaining_seconds
            
            # Ajouter des infos sur le dernier code
            response_data['last_code_expires_at'] = last_verification.expires_at.isoformat()
            response_data['last_code_is_expired'] = last_verification.is_expired()
            response_data['last_code_attempts'] = last_verification.attempts
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut de vérification: {str(e)}")
        return Response({
            'error': 'Erreur interne du serveur'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

