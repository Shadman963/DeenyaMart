from django.contrib import admin
from .models import ContentPage, NewsletterSubscriber, SiteSettings

admin.site.register(SiteSettings)
admin.site.register(ContentPage)
admin.site.register(NewsletterSubscriber)
