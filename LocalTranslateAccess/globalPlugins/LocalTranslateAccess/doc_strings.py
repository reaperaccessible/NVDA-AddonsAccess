"""Translatable short help, used when packaging localized documentation."""
from .i18n import _

def help_content():
    return [
        ('h1',_('LocalTranslate : traduction locale pour NVDA')),
        ('p',_('LocalTranslate traduit les textes annoncés par NVDA sur votre ordinateur, pour une application ou pour toutes les applications. Choisissez vos langues et un modèle compatible. Internet sert à télécharger les modèles ; les textes à traduire ne sont pas envoyés à un service en ligne.')),
        ('h2',_('Raccourcis')),
        ('p',_('{application_shortcut} : activer ou désactiver la traduction pour l’application courante. {global_shortcut} : activer ou désactiver la traduction globale. Vous pouvez modifier ces raccourcis dans les Gestes de commandes de NVDA.')),
        ('h2',_('Première utilisation')),
        ('p',_('Choisissez une ou plusieurs langues sources avec Espace, puis une langue cible. Ajoutez vos paires avec Ajouter. Sélectionnez ensuite un modèle compatible dans la liste, classée par taille. Choisissez Télécharger si nécessaire : le bouton indique la progression totale de 0 à 100 %. Une fois les modèles prêts, choisissez Appliquer pour enregistrer vos choix et activer la traduction.')),
        ('h2',_('Configuration')),
        ('p',_('Ouvrez le menu NVDA, Préférences, LocalTranslate. Dans Langues, modifiez la langue source, la langue cible et le modèle pour la traduction globale ou pour une application. Choisissez Appliquer pour enregistrer. La langue cible initiale suit la langue de la voix active de NVDA, ou celle de son interface si la voix ne fournit pas cette information.')),
        ('p',_('Une langue source choisie manuellement a priorité. En Détection automatique, la langue du document est utilisée lorsqu’elle est disponible ; sinon, le texte est analysé. La mémorisation de la langue d’une application est facultative et désactivée par défaut. Le modèle doit couvrir les langues à traduire.')),
        ('h2',_('Modèles installés')),
        ('p',_('Par défaut, les modèles utilisent le processeur et la RAM, y compris Hy-MT2 de 8 Go. Après la liste des modèles, la case Utiliser la carte graphique pour accélérer la traduction apparaît si une carte NVIDIA dédiée compatible dispose de suffisamment de VRAM libre. Cochez-la pour utiliser le GPU et la VRAM. Le choix est enregistré séparément pour chaque application et pour la traduction globale. Les bibliothèques nécessaires sont téléchargées automatiquement ; le modèle reste le même. Si le GPU devient indisponible, le processeur est utilisé lorsque la RAM le permet.')),
        ('p',_('Cet onglet présente les modèles installés, leurs langues et leur taille. Vous pouvez supprimer un modèle ; le cache des traductions est conservé. Les modèles et le cache sont stockés dans le dossier de l’addon.')),
        ('h2',_('Cache de traduction')),
        ('p',_('Choisissez une application, recherchez un texte et sélectionnez une traduction. Le texte d’origine est en lecture seule. Modifiez la traduction puis choisissez Enregistrer la correction. Les corrections et les traductions sont conservées après le redémarrage de NVDA et peuvent servir avec un autre modèle.')),
        ('p',_('Exporter un cache permet de partager le cache global, celui de toutes les applications ou celui des applications sélectionnées. Toutes les applications est décoché par défaut à l’exportation. Les textes d’origine, les traductions et les corrections sont inclus : vérifiez leur contenu avant de partager le fichier. Importer un cache permet de récupérer ces traductions ; vos corrections personnelles existantes sont conservées.')),
        ('h2',_('Performances')),
        ('p',_('Les mesures sur CPU et sur GPU sont séparées. Le GPU peut réduire le temps de traduction, mais le gain dépend du modèle et du texte. Le chargement initial peut être plus long.')),
        ('p',_('Cet onglet indique le temps de chargement du modèle et le temps moyen de traduction, séparé par modèle, langue source et langue cible. En mode automatique, le temps de détection est indiqué séparément. Les traductions retrouvées dans le cache sont exclues des moyennes.')),
    ]
