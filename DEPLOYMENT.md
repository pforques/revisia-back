# Guide de déploiement

## Configuration des fichiers statiques

### Problème résolu
Les fichiers CSS de l'admin Django n'apparaissaient pas en production avec Nixpacks.

### Solution implémentée

1. **WhiteNoise** : Ajouté pour servir les fichiers statiques en production
2. **Configuration STATIC** : Configuré `STATIC_ROOT` et `STATIC_URL`
3. **Procfile** : Ajouté `collectstatic` dans la phase de release
4. **Détection automatique** : Nixpacks détecte automatiquement Python et Django

### Variables d'environnement requises

```bash
DEBUG=False
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=your-domain.com
DATABASE_URL=postgresql://...
CORS_ALLOWED_ORIGINS=https://your-frontend.com
CSRF_TRUSTED_ORIGINS=https://your-backend.com
OPENAI_API_KEY=your-openai-key
```

### Commandes de déploiement

```bash
# Collecter les fichiers statiques
python manage.py collectstatic --noinput

# Migrations
python manage.py migrate

# Démarrer le serveur
gunicorn revisia_backend.wsgi:application --bind 0.0.0.0:$PORT --workers 3
```

### Vérification

Après déploiement, vérifier que :
- L'admin Django a ses styles CSS
- Les fichiers statiques sont servis correctement
- Les URLs `/static/` fonctionnent

## Rapport hebdo métriques (dimanche)

Commande ajoutée :

```bash
python manage.py send_weekly_metrics_report
```

Variables d'environnement :

```bash
WEEKLY_METRICS_REPORT_TO=ton-email@domaine.com
MAILGUN_FROM_EMAIL=noreply@mail.revisia-app.fr
```

Test manuel (sans envoi) :

```bash
python manage.py send_weekly_metrics_report --dry-run
```

Planification recommandée (chaque dimanche 09:00 UTC) :

```cron
0 9 * * 0 cd /path/to/revisia-back && /path/to/python manage.py send_weekly_metrics_report
```

### Résolution des erreurs de déploiement

#### Erreur "undefined variable 'pip'"
- **Cause** : Configuration Nixpacks incorrecte
- **Solution** : Supprimer `nixpacks.toml` et laisser la détection automatique

#### Erreur "No module named 'whitenoise'"
- **Cause** : WhiteNoise non installé localement
- **Solution** : `pip install whitenoise` dans l'environnement virtuel

#### Erreur WSGI
- **Cause** : Middleware WhiteNoise configuré mais package non installé
- **Solution** : Installer WhiteNoise avant de configurer le middleware
