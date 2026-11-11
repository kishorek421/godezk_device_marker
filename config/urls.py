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
    path('health/', views.health),
    path('', views.home),
    path('workspaces/create/', views.workspace_create),
    path('projects/create/', views.project_create),
    path('projects/<int:pk>/', views.project_page),
    path('projects/<int:pk>/members/', views.member_add),
    path('projects/<int:pk>/assign/', views.bulk_assign),
    path('projects/<int:pk>/distribute/', views.distribute),
    path('projects/<int:pk>/geometry/', views.geometry_set),
    path('projects/<int:pk>/export/', views.export_zip),
    path('projects/<int:pk>/review-next/', views.review_next),
    path('projects/<int:pk>/spot-check/', views.spot_check),
    path('projects/<int:pk>/labels/', views.label_create),
    path('projects/<int:pk>/labels/<uuid:label_id>/archive/', views.label_archive),
    path('projects/<int:pk>/tokens/', views.token_create),
    path('projects/<int:pk>/tokens/<uuid:token_id>/revoke/', views.token_revoke),
    path('projects/<int:pk>/webhooks/', views.webhook_create),
    path('projects/<int:pk>/webhooks/<uuid:hook_id>/delete/', views.webhook_delete),
    path('projects/<int:pk>/webhooks/<uuid:hook_id>/ping/', views.webhook_ping),
    path('projects/<int:pk>/upload/', views.upload),
    path('projects/<int:pk>/releases/', views.release_create),
    path('tasks/<int:pk>/', views.editor),
    path('tasks/<int:pk>/asset/', views.asset),
    path('tasks/<int:pk>/save/', views.save),
    path('tasks/<int:pk>/assign/', views.assign),
    path('tasks/<int:pk>/review/', views.review),
    path('tasks/<int:pk>/comment/', views.comment),
    path('tasks/<int:pk>/restore/', views.restore),
    path('releases/<uuid:pk>/', views.release_download),
    path('releases/<uuid:pk>/coco/', views.release_coco),
    path('releases/<uuid:pk>/yolo/', views.release_yolo),
    path('api/v1/projects/', views.api_projects),
    path('api/v1/projects/<int:pk>/', views.api_project_detail),
    path('api/v1/projects/<int:pk>/tasks/', views.api_tasks),
    path('api/v1/tasks/<int:pk>/', views.api_task),
    path('api/v1/releases/<uuid:pk>/', views.api_release),
]
