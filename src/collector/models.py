from django.db import models
from hashlib import sha256

class PuzzlePiece(models.Model):
	url = models.URLField(verbose_name="Image url")
	hash = models.CharField(max_length=64, unique=True, verbose_name="SHA256 hash of the url", default=None, null=True, blank=True)
	submitter = models.CharField(max_length=64, verbose_name="Hash of submitter ip address", default="", blank=True)
	submission_date = models.DateTimeField(verbose_name="Submission date", auto_now_add=True)
	priority = models.PositiveIntegerField(verbose_name="Priority value in transcription queue", default=0)
	confidence = models.PositiveIntegerField(verbose_name="Confidence score", default=0, blank=True)

	def save(self, *args, **kwargs):
		self.hash = self.calculate_hash()
		super().save(*args, **kwargs)

	def calculate_hash(self):
		return sha256(str(self.url).encode('utf-8')).hexdigest()

class Transcription(models.Model):
	class Meta:
		indexes = [
			models.Index(fields=['ip_address'], name='ip_address_idx')
		]

	# Metadata
	puzzle_piece = models.ForeignKey(PuzzlePiece, on_delete=models.CASCADE, related_name="transcriptions", null=True, default=None)
	submitter = models.CharField(max_length=64, verbose_name="Hash of submitter ip address", default="", blank=True)
	submission_date = models.DateTimeField(verbose_name="Submission date", auto_now_add=True)
	# Flags
	bad_flag = models.BooleanField(verbose_name="Image is bad or hard to read", default=False)
	rotation_flag = models.BooleanField(verbose_name="Image is rotated", default="")
	# Center in short notation
	center = models.CharField(max_length=1, verbose_name="center", default="")
	# Walls
	wall1 = models.BooleanField(verbose_name="Wall 1 (top)", default=False)
	wall2 = models.BooleanField(verbose_name="Wall 2 (top-right)", default=False)
	wall3 = models.BooleanField(verbose_name="Wall 3 (bottom-right)", default=False)
	wall4 = models.BooleanField(verbose_name="Wall 4 (bottom)", default=False)
	wall5 = models.BooleanField(verbose_name="Wall 5 (bottom-left)", default=False)
	wall6 = models.BooleanField(verbose_name="Wall 6 (top-left)", default=False)
	# Links
	link1 = models.CharField(max_length=7, verbose_name="Link 1 (top)", default="")
	link2 = models.CharField(max_length=7, verbose_name="Link 2 (top-right)", default="")
	link3 = models.CharField(max_length=7, verbose_name="Link 3 (bottom-right)", default="")
	link4 = models.CharField(max_length=7, verbose_name="Link 4 (bottom)", default="")
	link5 = models.CharField(max_length=7, verbose_name="Link 5 (bottom-left)", default="")
	link6 = models.CharField(max_length=7, verbose_name="Link 6 (top-left)", default="")
	# SHA256 hash of 'center wallsAsBitstring link1 link2 link3 link4 link5 link6' for comparison
	hash = models.CharField(max_length=64, verbose_name="SHA256 hash for comparison", default="", null=True, blank=True)

	def save(self, *args, **kwargs):
		self.sanitize_fields()
		if self.bad_flag:
			self.hash = None
		else:
			self.hash = self.calculate_hash()
		super().save(*args, **kwargs)

	def calculate_hash(self):
		hashInput = (
			f"{self.center} "
			f"{str(int(self.wall1))}{str(int(self.wall2))}{str(int(self.wall3))}"
			f"{str(int(self.wall4))}{str(int(self.wall5))}{str(int(self.wall6))} "
			f"{self.link1} {self.link2} {self.link3} {self.link4} {self.link5} {self.link6}"
		).encode("utf-8")
		return sha256(hashInput).hexdigest()

	def sanitize_fields(self):
		fields = [ "center", "link1", "link2", "link3", "link4", "link5", "link6" ]
		for field in fields:
			self.__setattr__(field, self.__getattribute__(field).upper())
