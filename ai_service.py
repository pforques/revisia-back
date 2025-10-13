"""
Service pour l'intégration avec l'API OpenAI
"""
import os
import openai
from django.conf import settings
import json
import logging

logger = logging.getLogger(__name__)

class OpenAIService:
    def __init__(self):
        self.client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
    
    def generate_questions_from_document(self, file_path, document_title, question_count=5, difficulty='medium', education_level='', instructions=''):
        """
        Génère des questions QCM à partir d'un fichier directement transmis à l'IA
        """
        try:
            import base64
            
            logger.info(f"🚀 Début de génération IA pour le document: {document_title}")
            logger.info(f"📁 Chemin du fichier: {file_path}")
            logger.info(f"📊 Paramètres: {question_count} questions, difficulté {difficulty}, niveau {education_level}")
            logger.info(f"📝 Instructions personnalisées: {instructions[:100] if instructions else 'Aucune'}...")
            
            # Vérifier la clé API
            if not settings.OPENAI_API_KEY:
                logger.error("❌ Clé API OpenAI manquante dans les paramètres")
                raise Exception("Configuration OpenAI manquante")
            
            logger.info(f"🔑 Clé API OpenAI configurée: {settings.OPENAI_API_KEY[:10]}...")
            
            # Construire le contexte éducatif détaillé
            education_context = self._build_education_context(education_level)
            
            # Lire le fichier et l'encoder en base64
            logger.info(f"📖 Lecture du fichier: {file_path}")
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_base64 = base64.b64encode(file_data).decode('utf-8')
            
            logger.info(f"📏 Taille du fichier: {len(file_data)} bytes")
            logger.info(f"📏 Taille base64: {len(file_base64)} caractères")
            
            # Déterminer le type MIME du fichier
            file_extension = os.path.splitext(file_path)[1].lower()
            mime_types = {
                '.pdf': 'application/pdf',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.txt': 'text/plain',
                '.md': 'text/markdown',
                '.json': 'application/json',
                '.csv': 'text/csv',
                '.xml': 'application/xml',
                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            }
            
            mime_type = mime_types.get(file_extension, 'application/octet-stream')
            logger.info(f"🏷️ Extension: {file_extension}, Type MIME: {mime_type}")
            
            # Construire les instructions personnalisées
            custom_instructions = ""
            if instructions and instructions.strip():
                custom_instructions = f"""

Instructions personnalisées de l'utilisateur:
{instructions.strip()}

IMPORTANT: Respecte ces instructions personnalisées lors de la génération des questions. Elles ont la priorité sur les instructions générales ci-dessous.
"""

            # Prompt pour générer des questions QCM
            prompt = f"""
Tu es un expert en pédagogie et en didactique. Génère {question_count} questions à choix multiples (QCM) de haute qualité basées sur le document suivant.

Titre du document: {document_title}

{education_context}{custom_instructions}

Instructions détaillées:
- Génère exactement {question_count} questions QCM
- Niveau de difficulté demandé: {difficulty}
- Chaque question doit avoir 4 options (A, B, C, D)
- Une seule réponse correcte par question
- Les questions doivent tester différents niveaux de compréhension :
  * Mémorisation (faits, définitions)
  * Compréhension (explications, relations)
  * Application (utilisation des concepts)
  * Analyse (comparaison, distinction)

Qualité des questions:
- Utilise un vocabulaire adapté au niveau d'éducation
- Les distracteurs (mauvaises réponses) doivent être plausibles mais incorrects
- Évite les questions trop évidentes ou piégeuses
- Varie les types de questions (définition, application, calcul, etc.)
- Assure-toi que la réponse correcte est clairement la meilleure

Format de réponse: JSON avec la structure suivante:
{{
    "questions": [
        {{
            "question_text": "Texte de la question claire et précise",
            "difficulty": "{difficulty}",
            "answers": [
                {{"text": "Option A - réponse plausible", "is_correct": true}},
                {{"text": "Option B - distracteur plausible", "is_correct": false}},
                {{"text": "Option C - distracteur plausible", "is_correct": false}},
                {{"text": "Option D - distracteur plausible", "is_correct": false}}
            ]
        }}
    ]
}}

Réponds UNIQUEMENT avec le JSON, sans texte supplémentaire.
"""

            # Uploader le fichier d'abord
            logger.info(f"📤 Upload du fichier vers OpenAI...")
            uploaded_file = self.client.files.create(
                file=open(file_path, 'rb'),
                purpose='assistants'
            )
            logger.info(f"✅ Fichier uploadé avec l'ID: {uploaded_file.id}")
            
            # Construire le message avec le fichier
            message_content = [
                {"type": "text", "text": prompt}
            ]
            
            # Ajouter le fichier selon son type
            if file_extension in ['.pdf', '.txt', '.md', '.json', '.csv', '.xml', '.pptx', '.xlsx']:
                # Pour les documents, utiliser le type "file"
                logger.info(f"📄 Ajout du document de type: {mime_type}")
                message_content.append({
                    "type": "file",
                    "file": {
                        "file_id": uploaded_file.id
                    }
                })
            else:
                # Pour les images, utiliser le type "image_url"
                logger.info(f"🖼️ Ajout de l'image de type: {mime_type}")
                message_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{file_base64}"
                    }
                })

            logger.info(f"🤖 Envoi de la requête à OpenAI avec le modèle gpt-4o-mini")
            logger.info(f"📝 Contenu du message: {len(message_content)} éléments")
            
            # Calculer max_tokens de manière très généreuse pour éviter les coupures
            # Estimation large : 150 tokens par question + 1000 tokens de marge
            estimated_tokens = (question_count * 150) + 1000
            max_tokens = min(max(estimated_tokens, 2000), 8000)  # Entre 2000 et 8000 tokens
            
            logger.info(f"🎯 Max tokens généreux: {max_tokens} pour {question_count} questions (estimation: {estimated_tokens})")
            
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Tu es un expert en pédagogie, didactique et évaluation. Tu génères des questions de quiz de haute qualité, adaptées au niveau d'éducation de l'utilisateur. Tu maîtrises les principes de la taxonomie de Bloom et adaptes le vocabulaire et la complexité selon le public cible. IMPORTANT: Tu dois toujours retourner un JSON valide et complet, même si tu dois réduire le nombre de questions pour respecter les limites de tokens."},
                    {"role": "user", "content": message_content}
                ],
                max_tokens=max_tokens,
                temperature=0.6
            )
            
            logger.info(f"✅ Réponse reçue d'OpenAI")
            
            # Extraire le contenu de la réponse
            content = response.choices[0].message.content.strip()
            logger.info(f"📄 Contenu brut reçu: {content[:200]}...")
            
            # Nettoyer le contenu (enlever les markdown si présent)
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            if content.startswith('```'):
                content = content[3:]
            
            # Nettoyer les espaces et caractères indésirables
            content = content.strip()
            
            logger.info(f"🧹 Contenu nettoyé: {content[:200]}...")
            
            # Vérifier si le JSON semble complet
            if not content.endswith('}'):
                logger.warning("⚠️ Le JSON semble incomplet (ne se termine pas par '}')")
                # Essayer de compléter le JSON
                if content.count('{') > content.count('}'):
                    missing_braces = content.count('{') - content.count('}')
                    content += '}' * missing_braces
                    logger.info(f"🔧 Ajout de {missing_braces} accolades fermantes")
            
            # Parser le JSON avec gestion d'erreur améliorée
            try:
                questions_data = json.loads(content)
            except json.JSONDecodeError as json_error:
                logger.error(f"❌ Erreur de parsing JSON: {json_error}")
                logger.error(f"📍 Position de l'erreur: ligne {json_error.lineno}, colonne {json_error.colno}")
                logger.error(f"📄 Contenu autour de l'erreur: {content[max(0, json_error.pos-50):json_error.pos+50]}")
                
                # Essayer de réparer le JSON
                try:
                    # Supprimer les caractères problématiques
                    import re
                    # Remplacer les guillemets simples par des guillemets doubles
                    content = re.sub(r"'([^']*)':", r'"\1":', content)
                    # Remplacer les guillemets simples dans les valeurs
                    content = re.sub(r':\s*\'([^\']*)\'', r': "\1"', content)
                    
                    logger.info("🔧 Tentative de réparation du JSON...")
                    questions_data = json.loads(content)
                    logger.info("✅ JSON réparé avec succès!")
                except:
                    logger.error("❌ Impossible de réparer le JSON")
                    raise Exception(f"Erreur de parsing JSON de la réponse IA: {json_error}")
            
            # Vérifier la structure du JSON
            if 'questions' not in questions_data:
                logger.error("❌ Structure JSON invalide: clé 'questions' manquante")
                raise Exception("Structure JSON invalide: clé 'questions' manquante")
            
            if not isinstance(questions_data['questions'], list):
                logger.error("❌ Structure JSON invalide: 'questions' n'est pas une liste")
                raise Exception("Structure JSON invalide: 'questions' n'est pas une liste")
            
            logger.info(f"✅ JSON parsé avec succès, {len(questions_data['questions'])} questions générées")
            
            # Nettoyer le fichier uploadé
            try:
                self.client.files.delete(uploaded_file.id)
                logger.info(f"🗑️ Fichier temporaire supprimé: {uploaded_file.id}")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Impossible de supprimer le fichier temporaire: {cleanup_error}")
            
            return questions_data['questions']
            
        except openai.AuthenticationError as e:
            logger.error(f"❌ Erreur d'authentification OpenAI: {e}")
            raise Exception("Erreur d'authentification avec l'API OpenAI. Vérifiez la configuration de la clé API.")
            
        except openai.RateLimitError as e:
            logger.error(f"⏰ Limite de taux OpenAI atteinte: {e}")
            raise Exception("Limite de requêtes atteinte. Veuillez réessayer dans quelques minutes.")
            
        except openai.APIError as e:
            logger.error(f"🔌 Erreur API OpenAI: {e}")
            raise Exception("Erreur temporaire de l'API OpenAI. Veuillez réessayer.")
            
        except Exception as e:
            logger.error(f"❌ Erreur inattendue lors de la génération IA: {e}")
            logger.error(f"📋 Type d'erreur: {type(e).__name__}")
            logger.error(f"📋 Détails: {str(e)}")
            
            # Nettoyer le fichier uploadé en cas d'erreur
            try:
                if 'uploaded_file' in locals():
                    self.client.files.delete(uploaded_file.id)
                    logger.info(f"🗑️ Fichier temporaire supprimé après erreur: {uploaded_file.id}")
            except Exception as cleanup_error:
                logger.error(f"❌ Erreur lors du nettoyage: {cleanup_error}")
                
            # Messages d'erreur plus spécifiques
            if "API key" in str(e).lower():
                raise Exception("Clé API OpenAI manquante ou invalide. Contactez l'administrateur.")
            elif "quota" in str(e).lower() or "limit" in str(e).lower():
                raise Exception("Quota OpenAI dépassé. Veuillez réessayer plus tard.")
            elif "timeout" in str(e).lower():
                raise Exception("Délai d'attente dépassé. Le document est peut-être trop volumineux.")
            else:
                raise Exception(f"Erreur lors de la génération des questions: {str(e)}")
    
    def _build_education_context(self, education_level):
        """Construit un contexte éducatif détaillé basé sur le niveau d'éducation"""
        if not education_level:
            return "Niveau d'éducation: Non spécifié - Adapte le contenu à un niveau général."
        
        # Mapping des niveaux vers des contextes détaillés
        education_contexts = {
            # Collège
            "6ème": "Niveau: 6ème (11-12 ans) - Utilise un vocabulaire simple, des concepts concrets, évite le jargon technique. Questions basées sur la mémorisation et la compréhension de base.",
            "5ème": "Niveau: 5ème (12-13 ans) - Vocabulaire accessible, concepts progressivement plus abstraits. Mélange mémorisation et compréhension.",
            "4ème": "Niveau: 4ème (13-14 ans) - Vocabulaire de niveau collège, introduction de concepts plus complexes. Questions de compréhension et d'application basique.",
            "3ème": "Niveau: 3ème (14-15 ans) - Vocabulaire de fin de collège, concepts abstraits maîtrisés. Questions d'application et d'analyse simple.",
            
            # Lycée
            "2nde": "Niveau: 2nde (15-16 ans) - Vocabulaire lycéen, concepts abstraits. Questions de compréhension, application et analyse.",
            "1ère": "Niveau: 1ère (16-17 ans) - Vocabulaire spécialisé selon la matière, concepts avancés. Questions d'analyse et de synthèse.",
            "Terminale": "Niveau: Terminale (17-18 ans) - Vocabulaire expert, concepts complexes. Questions d'analyse, synthèse et évaluation.",
            "Bac Pro": "Niveau: Bac Pro - Vocabulaire professionnel, applications concrètes. Questions pratiques et techniques.",
            "Bac Techno": "Niveau: Bac Techno - Vocabulaire technique spécialisé, applications sectorielles. Questions techniques et appliquées.",
            "CAP": "Niveau: CAP - Vocabulaire professionnel de base, applications pratiques. Questions concrètes et opérationnelles.",
            
            # Supérieur
            "BTS": "Niveau: BTS - Vocabulaire professionnel avancé, applications sectorielles. Questions techniques et professionnelles.",
            "DUT": "Niveau: DUT - Vocabulaire technique spécialisé, applications industrielles. Questions techniques et méthodologiques.",
            "BUT": "Niveau: BUT - Vocabulaire technique expert, applications professionnelles. Questions techniques avancées et méthodologiques.",
            "Licence": "Niveau: Licence - Vocabulaire académique, concepts théoriques. Questions d'analyse, synthèse et critique.",
            "Licence Pro": "Niveau: Licence Pro - Vocabulaire professionnel expert, applications avancées. Questions techniques et managériales.",
            "Master": "Niveau: Master - Vocabulaire académique expert, concepts avancés. Questions de recherche, analyse critique et innovation.",
            "Master Pro": "Niveau: Master Pro - Vocabulaire professionnel expert, applications stratégiques. Questions de management et d'innovation.",
            "Doctorat": "Niveau: Doctorat - Vocabulaire scientifique expert, concepts de pointe. Questions de recherche, innovation et contribution au savoir.",
            "École d'ingénieur": "Niveau: École d'ingénieur - Vocabulaire technique expert, applications industrielles. Questions techniques, méthodologiques et d'innovation.",
            "École de commerce": "Niveau: École de commerce - Vocabulaire business expert, applications stratégiques. Questions de management, stratégie et leadership.",
            "École spécialisée": "Niveau: École spécialisée - Vocabulaire expert du domaine, applications professionnelles. Questions spécialisées et pratiques.",
            "Formation continue": "Niveau: Formation continue - Vocabulaire professionnel adapté, applications pratiques. Questions opérationnelles et d'amélioration.",
            
            # Professionnel
            "En activité": "Niveau: Professionnel en activité - Vocabulaire professionnel, applications pratiques. Questions opérationnelles et d'efficacité.",
            "En recherche d'emploi": "Niveau: Professionnel en recherche - Vocabulaire professionnel, applications pratiques. Questions de compétences et d'adaptation.",
            "Retraité": "Niveau: Retraité - Vocabulaire accessible, applications concrètes. Questions de compréhension et d'application basique.",
            
            # Autre
            "Autre": "Niveau: Autre - Adapte le contenu à un niveau général accessible. Questions de compréhension et d'application."
        }
        
        return education_contexts.get(education_level, f"Niveau: {education_level} - Adapte le contenu à ce niveau spécifique.")
    
    def generate_lesson_title(self, file_path, document_title, education_level=''):
        """
        Génère un titre de leçon intelligent basé sur le contenu du document
        """
        try:
            import base64
            
            logger.info(f"🎯 Génération du titre de leçon pour: {document_title}")
            logger.info(f"📁 Chemin du fichier: {file_path}")
            
            # Vérifier la clé API
            if not settings.OPENAI_API_KEY:
                logger.error("❌ Clé API OpenAI manquante dans les paramètres")
                raise Exception("Configuration OpenAI manquante")
            
            # Construire le contexte éducatif
            education_context = self._build_education_context(education_level)
            
            # Lire le fichier et l'encoder en base64
            logger.info(f"📖 Lecture du fichier pour génération du titre: {file_path}")
            with open(file_path, 'rb') as f:
                file_data = f.read()
                file_base64 = base64.b64encode(file_data).decode('utf-8')
            
            # Déterminer le type MIME du fichier
            file_extension = os.path.splitext(file_path)[1].lower()
            mime_types = {
                '.pdf': 'application/pdf',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.txt': 'text/plain',
                '.md': 'text/markdown',
                '.json': 'application/json',
                '.csv': 'text/csv',
                '.xml': 'application/xml',
                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            }
            
            mime_type = mime_types.get(file_extension, 'application/octet-stream')
            
            # Prompt pour générer un titre de leçon
            prompt = f"""
Tu es un expert en pédagogie et en didactique. Analyse le document suivant et génère un titre de leçon intelligent et descriptif qui reflète le contenu principal du document.

Titre actuel du document: {document_title}

{education_context}

Instructions pour le titre:
- Le titre doit être clair, concis et informatif (maximum 60 caractères)
- Il doit refléter le sujet principal ou le thème central du document
- Utilise un vocabulaire adapté au niveau d'éducation
- Évite les termes techniques trop complexes si le niveau est basique
- Le titre doit être attractif et donner envie d'apprendre
- Si le document traite de plusieurs sujets, choisis le plus important
- Utilise des termes pédagogiques appropriés (ex: "Introduction à...", "Les bases de...", "Comprendre...")

Exemples de bons titres:
- "Introduction aux équations du second degré"
- "Les bases de la géométrie euclidienne"
- "Comprendre la photosynthèse"
- "Histoire de la Révolution française"
- "Les principes de la mécanique quantique"

Format de réponse: JSON avec la structure suivante:
{{
    "lesson_title": "Titre intelligent et descriptif de la leçon",
    "subject": "Matière principale identifiée",
    "keywords": ["mot-clé1", "mot-clé2", "mot-clé3"]
}}

Réponds UNIQUEMENT avec le JSON, sans texte supplémentaire.
"""

            # Uploader le fichier d'abord
            logger.info(f"📤 Upload du fichier pour génération du titre...")
            uploaded_file = self.client.files.create(
                file=open(file_path, 'rb'),
                purpose='assistants'
            )
            logger.info(f"✅ Fichier uploadé avec l'ID: {uploaded_file.id}")
            
            # Construire le message avec le fichier
            message_content = [
                {"type": "text", "text": prompt}
            ]
            
            # Ajouter le fichier selon son type
            if file_extension in ['.pdf', '.txt', '.md', '.json', '.csv', '.xml', '.pptx', '.xlsx']:
                message_content.append({
                    "type": "file",
                    "file": {
                        "file_id": uploaded_file.id
                    }
                })
            else:
                message_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{file_base64}"
                    }
                })

            logger.info(f"🤖 Envoi de la requête pour génération du titre...")
            
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Tu es un expert en pédagogie et en création de titres éducatifs. Tu analyses des documents et génères des titres de leçons clairs, attractifs et pédagogiquement pertinents. IMPORTANT: Tu dois toujours retourner un JSON valide avec le titre généré."},
                    {"role": "user", "content": message_content}
                ],
                max_tokens=200,
                temperature=0.7
            )
            
            logger.info(f"✅ Réponse reçue pour le titre")
            
            # Extraire le contenu de la réponse
            content = response.choices[0].message.content.strip()
            logger.info(f"📄 Contenu brut reçu: {content}")
            
            # Nettoyer le contenu (enlever les markdown si présent)
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            if content.startswith('```'):
                content = content[3:]
            
            content = content.strip()
            
            # Parser le JSON
            try:
                title_data = json.loads(content)
            except json.JSONDecodeError as json_error:
                logger.error(f"❌ Erreur de parsing JSON pour le titre: {json_error}")
                # Fallback: utiliser le titre du document
                return document_title
            
            # Nettoyer le fichier uploadé
            try:
                self.client.files.delete(uploaded_file.id)
                logger.info(f"🗑️ Fichier temporaire supprimé: {uploaded_file.id}")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Impossible de supprimer le fichier temporaire: {cleanup_error}")
            
            # Extraire le titre généré
            generated_title = title_data.get('lesson_title', document_title)
            logger.info(f"🎯 Titre généré: '{generated_title}'")
            
            return generated_title
            
        except Exception as e:
            logger.error(f"❌ Erreur lors de la génération du titre: {e}")
            # En cas d'erreur, retourner le titre du document
            return document_title

    def _get_fallback_questions(self, document_title, question_count, difficulty):
        """
        Questions de secours en cas d'erreur avec l'API
        """
        fallback_questions = [
            {
                "question_text": f"Quel est le sujet principal du document '{document_title}' ?",
                "difficulty": difficulty,
                "answers": [
                    {"text": "Le sujet principal du document", "is_correct": True},
                    {"text": "Un sujet secondaire", "is_correct": False},
                    {"text": "Une information accessoire", "is_correct": False},
                    {"text": "Une conclusion", "is_correct": False}
                ]
            },
            {
                "question_text": f"Le document '{document_title}' contient-il des informations importantes ?",
                "difficulty": difficulty,
                "answers": [
                    {"text": "Oui, des informations importantes", "is_correct": True},
                    {"text": "Non, aucune information", "is_correct": False},
                    {"text": "Seulement des détails", "is_correct": False},
                    {"text": "Des informations obsolètes", "is_correct": False}
                ]
            }
        ]
        
        # Retourner le nombre demandé de questions
        return fallback_questions[:question_count]
