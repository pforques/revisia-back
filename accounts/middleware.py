"""
Middleware pour vérifier que l'email de l'utilisateur est vérifié
"""
from django.http import JsonResponse, HttpResponseRedirect
from django.urls import resolve
from rest_framework import status
import logging

logger = logging.getLogger(__name__)

class EmailVerificationMiddleware:
    """
    Middleware qui bloque COMPLÈTEMENT l'accès à l'application 
    si l'email de l'utilisateur n'est pas vérifié
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # SEULS endpoints autorisés sans vérification email
        self.allowed_endpoints = [
            'register',
            'login', 
            'logout',
            'send_email_verification',
            'verify_email_code',
            'email_verification_status',
            'stripe_webhook',  # Webhook Stripe doit fonctionner
        ]

    def __call__(self, request):
        # Vérifier si c'est une requête API
        if not request.path.startswith('/api/'):
            return self.get_response(request)
        
        # Vérifier si l'utilisateur est authentifié
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)
        
        # Récupérer le nom de la vue
        try:
            resolver_match = resolve(request.path)
            view_name = resolver_match.url_name
        except:
            return self.get_response(request)
        
        # Vérifier si l'endpoint est dans la liste des endpoints autorisés
        if view_name in self.allowed_endpoints:
            return self.get_response(request)
        
        # BLOQUER TOUT LE RESTE si l'email n'est pas vérifié
        if not request.user.email_verified:
            logger.warning(f"Tentative d'accès à {view_name} par utilisateur non vérifié: {request.user.email}")
            return JsonResponse({
                'error': 'Email non vérifié',
                'message': 'Vous devez vérifier votre adresse email pour accéder à Révisia.',
                'email_verified': False,
                'email': request.user.email,
                'verification_required': True,
                'blocked_endpoint': view_name
            }, status=status.HTTP_403_FORBIDDEN)
        
        return self.get_response(request)

class FrontendEmailVerificationMiddleware:
    """
    Middleware pour bloquer l'accès au frontend si l'email n'est pas vérifié
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Pages autorisées sans vérification email
        self.allowed_pages = [
            '/login',
            '/register',
            '/verify-email',
        ]

    def __call__(self, request):
        # Vérifier si c'est une requête frontend (pas API, pas admin, pas static)
        if (request.path.startswith('/api/') or 
            request.path.startswith('/admin/') or 
            request.path.startswith('/static/') or
            request.path.startswith('/_next/') or
            request.path.startswith('/favicon') or
            request.path.startswith('/manifest') or
            request.path.startswith('/png/') or
            request.path.startswith('/svg/') or
            request.path.startswith('/videos/')):
            return self.get_response(request)
        
        # Vérifier si l'utilisateur est authentifié
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            return self.get_response(request)
        
        # Vérifier si c'est une page autorisée
        if request.path in self.allowed_pages:
            return self.get_response(request)
        
        # BLOQUER TOUT LE RESTE si l'email n'est pas vérifié
        if not request.user.email_verified:
            logger.warning(f"Tentative d'accès frontend à {request.path} par utilisateur non vérifié: {request.user.email}")
            # Rediriger vers la page de vérification email
            return HttpResponseRedirect('/verify-email')
        
        return self.get_response(request)