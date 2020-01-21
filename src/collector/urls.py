from django.urls import include, path
from rest_framework import routers

from . import views

api_router = routers.DefaultRouter()
api_router.register(r'pieces', views.PuzzlePieceViewSet)
api_router.register(r'transcriptions', views.TranscriptionViewSet)

urlpatterns = [
	path("", views.index, name="index"),
        path('api/', include(api_router.urls)),
	path("puzzlepieces/submit", views.submit_puzzle_piece, name="puzzlepieceSubmit"),
	path("puzzlepieces/", views.PuzzlePieceIndex.as_view(), name="puzzlepieceIndex"),
	path("puzzlepieces/<int:image_id>/", views.puzzle_piece_view, name="puzzlepieceView"),
	path("transcriptions", views.TranscriptionsIndex.as_view(), name="transcriptions"),
	path("transcriptions/<int:transcription_id>", views.transcriptions_detail, name="transcriptionsDetail"),
	path("transcriptions/guide", views.transcriptionGuide, name="transcriptionGuide"),
	path("transcribe", views.TranscribeIndex.as_view(), name="transcribe"),
	path("transcribe/<int:puzzlepiece_id>", views.process_transcription, name="transcribeResults"),
	path("solutions", views.ConfidenceSolutionIndex.as_view(), name="confidenceSolutionIndex"),
	path("export/verified/csv", views.exportVerifiedCSV, name="exportVerifiedCSV"),
	path("export/pieces/csv", views.exportPiecesCSV, name="exportPiecesCSV"),
	path("export/transcriptions/csv", views.exportTranscriptionsCSV, name="exportTranscriptionsCSV"),
]
