"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from studio import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', views.home),
    path('workspaces/create/', views.workspace_create),
    path('projects/create/', views.project_create),
    path('projects/<int:pk>/', views.project_page),
    path('projects/<int:pk>/members/', views.member_add),
    path('projects/<int:pk>/labels/', views.label_create),
    path('projects/<int:pk>/upload/', views.upload),
    path('projects/<int:pk>/releases/', views.release_create),
    path('tasks/<int:pk>/', views.editor),
    path('tasks/<int:pk>/asset/', views.asset),
    path('tasks/<int:pk>/save/', views.save),
    path('tasks/<int:pk>/assign/', views.assign),
    path('tasks/<int:pk>/review/', views.review),
    path('releases/<uuid:pk>/', views.release_download),
]
