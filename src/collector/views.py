from urllib.parse import urlparse
import csv
import requests
import re
import json

from django.http import HttpResponse
from django.http import Http404
from django.template import loader
from django.shortcuts import get_object_or_404, render
from django.views import generic
from django.views.decorators.cache import cache_page
from django.db.models import Count, F, Max
from django.utils.decorators import method_decorator
from django.conf import settings
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import mixins, status, viewsets

from .models import PuzzlePiece, Transcription
from .serializers import (
    PuzzlePieceSerializer,
    TranscriptionDataSerializer,
    BadImageSerializer,
    ConfidentSolutionSerializer,
)
from .utility import *

# Settings for the confidence and image distribution logic
DEFAULT_PUZZLE_PIECE_PRIORITY = 10
IMAGE_POOL_SIZE = 100
CONFIDENCE_RATIO = 80
MIN_TRANSCRIPTIONS = 3
MIN_ROTATED_TRANSCRIPTIONS = 5
BAD_FLAG_THRESHOLD = 2

# Regex pattern for text submissions
TEXT_SUBMISSION_PATTERN = re.compile(r"^\s*(?P<center>(Blank|Plus|Clover|Hex|Snake|Diamond|Cauldron|B|P|C|H|S|D|T))\s*(?P<sides>([1-6])(\s*,\s*[1-6]){0,5})(?P<links>(\s*[BPCHSDT]{7}){6})\s*$", re.IGNORECASE)

# Image url patterns used in find_image
IMAGE_URL_PATTERNS = ({
	"imgur.com": [ "https://i.imgur.com/{}.png", "https://i.imgur.com/{}.jpg", "https://i.imgur.com/{}.jpeg" ],
	"gyazo.com": [ "https://i.gyazo.com/{}.png", "https://i.gyazo.com/{}.jpg", "https://i.imgur.com/{}.jpeg" ]
})
IMAGE_URL_WHITELIST = ["cdn.discordapp.com", "media.discordapp.net", "i.gyazo.com", "gyazo.com", "i.imgur.com", "imgur.com"]

def find_image(url):
	host = url.hostname
	if host in IMAGE_URL_PATTERNS.keys():
		for url_pattern in IMAGE_URL_PATTERNS[host]:
			target_url = url_pattern.format(url.path)
			res = requests.head(target_url)
			if res.status_code == 200:
				return target_url
	return None

def find_unconfident_puzzle_piece(request):
	client_identifier = disguise_client_ip(get_client_ip(request))
	# We want to order by transCount descending to get faster results. We do not show anything definitely flagged as bad; that already has been solved
	result = PuzzlePiece.objects.raw(f"""
		SELECT * FROM
			(SELECT * FROM collector_puzzlepiece pp WHERE
				confidence < {CONFIDENCE_RATIO} AND
				(SELECT SUM(t.bad_flag) FROM collector_transcription t WHERE t.puzzle_piece_id = pp.id) < {BAD_FLAG_THRESHOLD} AND
				NOT EXISTS (SELECT t.id FROM collector_transcription t WHERE t.puzzle_piece_id = t.id AND t.submitter = {client_identifier})
				ORDER BY pp.priority DESC
				LIMIT {IMAGE_POOL_SIZE}
			) ct
		ORDER BY RAND()
		LIMIT 1
	""")

	if len(result) == 0:
		return None
	result = result[0]

	# Add an is_image that we'll reference in the template, this allows us to handle generic links
	result.is_image = is_image_url(urlparse(result.url))

	# Warn if rotated
	rotated = PuzzlePiece.objects.raw(f"""
		SELECT COUNT(id) FROM collector_transcription WHERE
			puzzle_piece_id = {result.id} AND
			rotation_flag = 1
	""")
	result.is_rotated = rotated > 0
	return result

@cache_page(60 * 60)
def index(request):
	template = loader.get_template("collector/index.html")
	return HttpResponse(template.render(None, request))

@cache_page(60 * 60)
def transcriptionGuide(request):
	template = loader.get_template("collector/transcriptionGuide.html")
	return HttpResponse(template.render(None, request))

def submit_puzzle_piece(request):
	responseMessage = None
	responseMessageSuccess = None

	try:
		if request.method == "POST":
			url = request.POST["url"].strip()
			if len(url) > 200:
				raise ValueError("You havin\' a laff, mate? A URL that long? Yeah no.")
			parsed_url = urlparse(url)
			if parsed_url.hostname not in IMAGE_URL_WHITELIST:
				raise ValueError(f"We only accept images from {', '.join(IMAGE_URL_WHITELIST)} right now.")
			# Try to convert to an image url if not already present
			if not is_image_url(parsed_url):
				target_url = find_image(parsed_url)
				if target_url:
					url = target_url
				else:
					raise ValueError('Please make sure your link ends with .jpg or .jpeg or .png. Direct links to images work best with our current site.')
			# Check if image is reachable
			res = requests.head(url)
			if res.status_code != 200:
				raise ValueError(url + ' -- That URL does not seem to exist. Please verify and try again.')

			new_piece = PuzzlePiece()
			new_piece.url = url
			# An IP is personal data as per GDPR, kid you not. Let's hash it, we just need something unique
			new_piece.submitter = disguise_client_ip(get_client_ip(request))
			new_piece.priority = DEFAULT_PUZZLE_PIECE_PRIORITY
			new_piece.save()

			responseMessageSuccess = "Puzzle Piece image submitted successfully!"
	except KeyError as ex:
		responseMessage = "There was an issue with your request. Please try again?"
	except ValueError as ex:
		responseMessage = str(ex)
	except Exception as ex:
		if "unique" in str(ex).lower() or "duplicate" in str(ex).lower():
			responseMessage = "We already had that. Try another!"
		else:
			responseMessage = "Something went wrong..." + str(ex)

	template = loader.get_template("collector/submit_piece.html")
	context = {
		"error_message": responseMessage,
		"success_message": responseMessageSuccess,
	}
	return HttpResponse(template.render(context, request))

@method_decorator(cache_page(60), name='dispatch')
class PuzzlePieceIndex(generic.ListView):
	template_name = 'collector/latest.html'
	context_object_name = 'latest'

	def get_queryset(self):
		return PuzzlePiece.objects.order_by("-submitted_date")[:50]

def puzzle_piece_view(request, image_id):
	piece = get_object_or_404(PuzzlePiece, pk=image_id)
	context = {
		"puzzlepiece": piece
	}
	return render(request, 'collector/puzzlepieceDetail.html', context)

class TranscribeIndex(generic.ListView):
	template_name = 'collector/transcribe.html'
	context_object_name = 'puzzlepiece'

	def get_queryset(self):
		return find_unconfident_puzzle_piece(self.request)

def process_transcription(request, puzzlepiece_id):
	data = None
	errors = None
	transcript_data = None

	if request.method == "POST":
		is_bad_image = request.POST["bad_image"] if "bad_image" in request.POST else False
		is_rotated_image = request.POST["rotated_image"] if "rotated_image" in request.POST else False
		data = request.POST["data"]
		try:
			data = json.loads(data)
		except Exception:
			# If parsing the data fails we know that the data might be provided as plain text in the known format
			match = TEXT_SUBMISSION_PATTERN.match(data)
			data = parse_plain_data(match) if match else None

		puzzlePiece = get_object_or_404(PuzzlePiece, pk=puzzlepiece_id)
		# Hash IP bcs of GDPR
		client_identifier = disguise_client_ip(get_client_ip(request))
		errors, transcript_data = process_transcription_data(data, is_bad_image, is_rotated_image, puzzlePiece, client_identifier)
		determineConfidence(puzzlepiece_id)

	context = {
		"data": data,
		"errors": errors,
		"transcript": transcript_data
	}
	return render(request, "collector/transcribeResults.html", context=context)

def parse_plain_data(matched_data):
    data_dict = {}

    # Sometimes the center is fully written out. Other times it is not.
    # This allows for both.
    data_dict["center"] = "T" if matched_data.group('center') == "Cauldron" else matched_data.group('center').upper()[0]

    # Wall list of length 6. Default is wall true, since string contains list of
    # openings.
    data_dict["walls"] = [True] * 6
    for opening in matched_data.group('sides').split(","):
        data_dict["walls"][int(opening) - 1] = False

    # Node list. Split string into list of strings, then split each side
    # into a list of characters.
    data_dict["nodes"] = []
    side_list = matched_data.group('links').split()
    for side in side_list:
        data_dict["nodes"].append(list(side.upper()))

    return data_dict

def process_transcription_data(raw_transcription, is_bad, is_rotated, puzzle_piece, client_identifier):
	transcriptData = Transcription()
	transcriptData.submitter = client_identifier
	transcriptData.puzzle_piece = puzzle_piece
	transcriptData.bad_flag = is_bad
	transcriptData.rotation_flag = is_rotated
	errors = []

	# If the image is marked as bad we can skip parsing the transcription and stick to the default values
	if not transcriptData.bad_flag:
		# Parse data from raw_transcription
		center = get_dict_value(raw_transcription, "center", None)
		walls = get_dict_value(raw_transcription, "walls", None)
		edges = get_dict_value(raw_transcription, "nodes", None)
		if not center:
			errors.append("No center value was found in the JSON. This is required.")
		if walls and len(walls) != 6:
			errors.append("There should be 6 walls in the JSON. {} were found.".format(len(walls)))
		if edges and len(edges) != 6:
			errors.append("There should be 6 edges/nodes in the JSON. {} were found.".format(len(edges)))
		# Set parsed data
		transcriptData.center = center
		for i in range(6):
			transcriptData.__setattr__(f"wall{str(i + 1)}", walls[i])
			transcriptData.__setattr__(f"link{str(i + 1)}", edges[i])

	transcriptData.save()
	return errors, transcriptData

@method_decorator(cache_page(60), name='dispatch')
class TranscriptionsIndex(generic.ListView):
	template_name = 'collector/transcriptions.html'
	context_object_name = 'latest'

	def get_queryset(self):
		return TranscriptionData.objects.order_by("-submitted_date")[:50]

def transcriptions_detail(request, transcription_id):
	transcription = get_object_or_404(Transcription, pk=transcription_id)
	context = {
		"transcription": transcription,
		"puzzlepiece": transcription.puzzle_piece
	}
	return render(request, 'collector/transcriptionDetail.html', context)

def determine_confidence(puzzle_piece_id):
	data = Transcription.objects.filter(puzzle_piece_id=puzzle_piece_id)

	hashes = {}
	bad_count = 0
	rotation_count = 0

	# Count transcriptions where the image had been marked as bad
	for d in data:
		if d.bad_image:
			bad_count += 1
	# Stop calculation when BAD_FLAG_THRESHOLD is reached
	if bad_count >= BAD_FLAG_THRESHOLD:
		return

	# Count transcription with reported image rotation
	for d in data:
		if d.rotation_flag:
			rotation_count += 1
	# Adjust valid_transcriptions, we will exclude bad image submissions from now on
	valid_transcriptions = len(data) - bad_count

	# Is there enough data to determine a confidence level?
	# If no, create or update a tracker entry.
	if (rotation_count == 0 and valid_transcriptions < MIN_TRANSCRIPTIONS) or (rotation_count > 0 and valid_transcriptions < MIN_ROTATED_TRANSCRIPTIONS):
		return

	for d in data:
		if d.hash not in hashes:
			hashes[d.hash] = 0
		hashes[d.hash] += 1

	most_common_hash_count = 0
	for _, count in hashes.items():
		if count > most_common_hash_count:
			most_common_hash_count = count

	confidence = (most_common_hash_count / valid_transcriptions) * 100
	# Update the confidence on the puzzle piece
	upsert_confidence(puzzle_piece_id, confidence)

def upsert_confidence(puzzle_piece_id, confidence):
	puzzle_piece = get_object_or_404(PuzzlePiece, pk=puzzle_piece_id)
	puzzle_piece.confidence = confidence
	puzzle_piece.save()

@method_decorator(cache_page(60), name='dispatch')
class ConfidenceSolutionIndex(generic.ListView):
	template_name = 'collector/confidenceSolutionIndex.html'
	context_object_name = "collection"

	def get_queryset(self):
		return PuzzlePiece.objects.filter(confidence__gte=CONFIDENCE_RATIO)


class PuzzlePieceViewSet(viewsets.ReadOnlyModelViewSet):
	# annotate badimages count for serializer performance
	queryset = PuzzlePiece.objects.all()
	serializer_class = PuzzlePieceSerializer

	@action(detail=False)
	def get_random(self, request):
		unconfident_piece = find_unconfident_puzzle_piece(request)
		serializer = self.get_serializer(unconfident_piece)
		return Response(serializer.data)

class TranscriptionViewSet(viewsets.GenericViewSet, mixins.CreateModelMixin):
    queryset = Transcription.objects.all()
    serializer_class = TranscriptionDataSerializer

    # copied code from the mixin, but we need access to request here
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client_identifier = disguise_client_ip(get_client_ip(request))
        serializer.save(submitter=client_identifier)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

@cache_page(60 * 5)
def exportVerifiedCSV(request):
	response = HttpResponse(content_type = 'text/csv')
	response['Content-Disposition'] = 'attachment; filename="verified.csv"'
	writer = csv.writer(response)

	writer.writerow([
		"Image",
		"Center",
		"Openings",
		"Link1",
		"Link2",
		"Link3",
		"Link4",
		"Link5",
		"Link6",
		"Confidence",
		"Transcription hash",
		"Transcription count",
		"Incorrect Rotation Flag"
	])

	for solution in ConfidentSolution.objects.all():
		walls = [solution.wall1, solution.wall2, solution.wall3, solution.wall4, solution.wall5, solution.wall6]
		openings = ",".join(str(i+1) for i in range(6) if not walls[i])

		rotated = PuzzlePiece.objects.raw('SELECT id FROM collector_rotatedimage WHERE puzzlePiece_id = ' + str(solution.puzzlePiece.id))
		if rotated:
			solution.rotated = True
		else:
			solution.rotated = False
		writer.writerow([
			solution.puzzlePiece.url,
			solution.center,
			openings,
			solution.link1,
			solution.link2,
			solution.link3,
			solution.link4,
			solution.link5,
			solution.link6,
			solution.confidence,
			solution.datahash,
			solution.puzzlePiece.transCount,
			solution.rotated
		])

	return response

@cache_page(60 * 5)
def exportPiecesCSV(request):
	response = HttpResponse(content_type = 'text/csv')
	response['Content-Disposition'] = 'attachment; filename="imgurls.csv"'
	writer = csv.writer(response)

	writer.writerow([
		"Image",
		"Submitter",
		"Submitted date",
		"Last modified",
		"Transcription count"
	])

	for piece in PuzzlePiece.objects.all():
		writer.writerow([
			piece.url,
			secretly_hash_my_data(piece.ip_address),
			piece.submitted_date,
			piece.last_modified,
			piece.transCount
		])

	return response

@cache_page(60 * 5)
def exportTranscriptionsCSV(request):
	response = HttpResponse(content_type = 'text/csv')
	response['Content-Disposition'] = 'attachment; filename="transcriptions.csv"'
	writer = csv.writer(response)

	writer.writerow([
		"Image",
		"Submitter",
		"Submitted date",
		"Bad image",
		"Orientation",
		"Center",
		"Openings",
		"Link1",
		"Link2",
		"Link3",
		"Link4",
		"Link5",
		"Link6",
		"Transcription hash"
	])

	for trans in TranscriptionData.objects.all():
		walls = [trans.wall1, trans.wall2, trans.wall3, trans.wall4, trans.wall5, trans.wall6]
		openings = ",".join(str(i+1) for i in range(6) if not walls[i])

		writer.writerow([
			trans.puzzlePiece.url,
			secretly_hash_my_data(trans.ip_address),
			trans.submitted_date,
			trans.bad_image,
			trans.orientation,
			trans.center,
			openings,
			trans.link1,
			trans.link2,
			trans.link3,
			trans.link4,
			trans.link5,
			trans.link6,
			trans.datahash
		])

	return response
