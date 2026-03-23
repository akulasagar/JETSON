import requests
import json
import time
import threading
import queue
import os
import uuid
from datetime import datetime, date, timedelta
import logging
from urllib.parse import urlencode, urlparse
from azure.storage.blob import BlobServiceClient, ContentSettings, BlobBlock
import mimetypes
import traceback
from enum import Enum
import math
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Custom JSON encoder to handle datetime objects
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

# Upload status enumeration
class UploadStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    AZURE_UPLOADING = "azure_uploading"
    AZURE_COMPLETE = "azure_complete"
    API_SENDING = "api_sending"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"
    CANCELLED = "cancelled"
    PARTIAL_SUCCESS = "partial_success"


class Uploader:
    def __init__(
        self,
        api_url=None,
        max_retries=1,
        retry_delay=5
    ):
        """
        Initialize the Uploader with API endpoint and retry settings
        """
        # Load from .env if not provided
        if not api_url:
            api_url = os.getenv('API_URL') or os.getenv('BACKEND_URL') or ""
            
        self.api_url = api_url
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Operation queue for asynchronous uploads
        self.operation_queue = queue.Queue()

        # Failed operations queue for retry
        self.failed_queue = queue.Queue()

        # In-progress operations tracking
        self.in_progress_operations = {}

        # Azure Blob Service Client
        self.blob_service_client = None
        self.container_client = None
        self.azure_initialized = False

        # Chunk upload settings - optimized for 100MB+ videos
        self.chunk_size = 4 * 1024 * 1024  # 4MB chunks (optimal for Azure)
        self.max_simple_upload_size = 8 * 1024 * 1024  # 8MB max for simple upload (REDUCED from 256MB)
        self.video_chunk_threshold = 10 * 1024 * 1024  # 10MB - Always chunk videos above this size

        # Flag to control upload thread
        self.is_running = False
        self.upload_thread = None

        # Initialize logger with detailed format
        self.logger = logging.getLogger(__name__)

        # Status callback function (can be set from main app)
        self.status_callback = None

        # Statistics with detailed tracking
        self.stats = {
            'total_attempted': 0,
            'successful': 0,
            'failed': 0,
            'queued': 0,
            'azure_uploads': 0,
            'azure_failed': 0,
            'api_errors': 0,
            'connection_errors': 0,
            'timeouts': 0,
            'validation_errors': 0,
            'manhole_cleaning': 0,
            'pipe_inspection': 0,
            'chunked_uploads': 0,
            'direct_uploads': 0,
            'streaming_uploads': 0,
            'partial_successes': 0,
            'start_time': datetime.now()
        }

        # Performance metrics
        self.performance = {
            'avg_upload_time': 0,
            'total_upload_time': 0,
            'fastest_upload': float('inf'),
            'slowest_upload': 0,
            'upload_count': 0,
            'avg_chunks_per_upload': 0,
            'total_chunks_uploaded': 0
        }

        # Detailed operation history
        self.operation_history = []
        self.max_history = 100

        # Start upload thread
        self.start_upload_thread()

        self.logger.info(f"[UPLOADER] Initialized with API: {api_url}")
        self.logger.info(
            f"[UPLOADER] Max retries: {max_retries}, Retry delay: {retry_delay}s")
        self.logger.info(
            f"[UPLOADER] Chunk size: {self.chunk_size/1024/1024}MB, Max simple upload: {self.max_simple_upload_size/1024/1024}MB")
        self.logger.info(
            f"[UPLOADER] Video chunk threshold: {self.video_chunk_threshold/1024/1024}MB")

    def set_status_callback(self, callback):
        """Set callback function for status updates"""
        self.status_callback = callback
        self.logger.info("[UPLOADER] Status callback set")

    def _update_status(self, operation_id, status, message="", details=None):
        """Update operation status and notify callback"""
        timestamp = datetime.now()

        # Update in-progress tracking
        if operation_id not in self.in_progress_operations:
            self.in_progress_operations[operation_id] = {
                'operation_id': operation_id,
                'status': status.value,
                'message': message,
                'details': details or {},
                'timestamp': timestamp,
                'updates': []
            }

        self.in_progress_operations[operation_id].update({
            'status': status.value,
            'message': message,
            'details': details or {},
            'timestamp': timestamp
        })

        # Add to update history
        self.in_progress_operations[operation_id]['updates'].append({
            'time': timestamp,
            'status': status.value,
            'message': message
        })

        # Trim updates history
        if len(self.in_progress_operations[operation_id]['updates']) > 20:
            self.in_progress_operations[operation_id]['updates'] = self.in_progress_operations[operation_id]['updates'][-20:]

        # Log status change
        self.logger.info(
            f"[STATUS] {operation_id}: {status.value} - {message}")

        # Call status callback if set
        if self.status_callback:
            try:
                self.status_callback(
                    operation_id, status.value, message, details)
            except Exception as e:
                self.logger.error(f"[STATUS CALLBACK ERROR] {e}")

    def init_azure(self, config=None):
        """Initialize Azure Blob Storage client if configured"""
        try:
            # Use provided config or environment variables
            connection_string = (config or {}).get('azure_connection_string') or os.getenv('AZURE_CONNECTION_STRING') or ""
            container_name = (config or {}).get('azure_container_name') or os.getenv('AZURE_CONTAINER_NAME') or ""

            if connection_string and container_name:
                self.logger.info(
                    f"[AZURE] Initializing Azure with container: {container_name}")

                # Test connection string format
                if "DefaultEndpointsProtocol" not in connection_string:
                    self.logger.error(
                        "[AZURE] Invalid connection string format")
                    return False

                # Always use the simple initialization
                self.blob_service_client = BlobServiceClient.from_connection_string(connection_string)
                self.container_client = self.blob_service_client.get_container_client(container_name)

                # Try to check/create container (skip if it fails)
                try:
                    if not self.container_client.exists():
                        self.container_client.create_container(public_access='blob')
                        self.logger.info(f"[AZURE] Created container: {container_name}")
                    else:
                        self.logger.info(f"[AZURE] Container exists: {container_name}")
                except Exception as e:
                    self.logger.warning(f"[AZURE] Container check warning: {str(e)}")
                    # Continue anyway - container might already exist

                self.azure_initialized = True
                self.logger.info(f"[AZURE] Azure Blob Storage initialized for container: {container_name}")
                
                # Extract account name and key for SAS generation
                try:
                    parts = {p.split('=', 1)[0]: p.split('=', 1)[1] for p in connection_string.split(';') if '=' in p}
                    self.account_name = parts.get('AccountName')
                    self.account_key = parts.get('AccountKey')
                    self.container_name = container_name
                except Exception as e:
                    self.logger.warning(f"[AZURE] Could not parse credentials for SAS: {e}")

                return True
                
            else:
                self.logger.warning(
                    "[AZURE] Azure credentials not configured")
                return False

        except Exception as e:
            self.logger.error(
                f"[AZURE ERROR] Failed to initialize Azure: {str(e)}")
            self.logger.error(
                f"[AZURE ERROR] Traceback: {traceback.format_exc()}")
            return False

    def _upload_file_in_chunks_streaming(self, file_path, blob_name, content_type):
        """Upload large file in chunks using streaming to avoid memory issues"""
        try:
            blob_client = self.container_client.get_blob_client(blob_name)
            file_size = os.path.getsize(file_path)
            chunk_size = self.chunk_size
            num_chunks = math.ceil(file_size / chunk_size)
            
            self.logger.info(f"[AZURE STREAMING] Uploading {file_size/1024/1024:.2f}MB in {num_chunks} chunks")
            
            block_list = []
            
            with open(file_path, "rb") as file:
                for i in range(num_chunks):
                    # Read only one chunk at a time
                    chunk = file.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Generate block ID
                    block_id = f"block{i:05d}".encode('utf-8')
                    
                    # Upload this single chunk with timeout
                    try:
                        blob_client.stage_block(block_id=block_id, data=chunk, timeout=60)
                    except Exception as e:
                        self.logger.error(f"[AZURE CHUNK ERROR] Failed to upload chunk {i}: {str(e)}")
                        # If chunk fails, retry once
                        try:
                            time.sleep(2)
                            blob_client.stage_block(block_id=block_id, data=chunk, timeout=60)
                        except Exception as retry_error:
                            self.logger.error(f"[AZURE CHUNK RETRY ERROR] Failed again: {str(retry_error)}")
                            return False
                    
                    block_list.append(BlobBlock(block_id=block_id))
                    
                    # Clear chunk from memory immediately
                    del chunk
                    
                    # Log progress every 10% or 10 chunks, whichever is smaller
                    if (i + 1) % max(1, min(10, num_chunks // 10)) == 0:
                        progress = (i + 1) / num_chunks * 100
                        mb_uploaded = (i + 1) * chunk_size / 1024 / 1024
                        self.logger.info(f"[AZURE STREAMING] Progress: {progress:.1f}% ({mb_uploaded:.1f}MB uploaded)")
            
            # Commit all blocks
            blob_client.commit_block_list(block_list)
            
            # Update stats
            self.stats['chunked_uploads'] += 1
            self.stats['streaming_uploads'] += 1
            self.performance['total_chunks_uploaded'] += num_chunks
            
            if num_chunks > 1:
                self.performance['avg_chunks_per_upload'] = (
                    self.performance['avg_chunks_per_upload'] * (self.stats['chunked_uploads'] - 1) + num_chunks
                ) / self.stats['chunked_uploads']
            
            return True
            
        except Exception as e:
            self.logger.error(f"[AZURE STREAMING ERROR] Failed: {str(e)}")
            self.logger.error(f"[AZURE STREAMING ERROR] Traceback: {traceback.format_exc()}")
            return False

    def _upload_data_in_chunks(self, data, blob_name, content_type='application/octet-stream'):
        """Upload data in chunks using block blob upload"""
        try:
            blob_client = self.container_client.get_blob_client(blob_name)
            data_size = len(data)
            chunk_size = self.chunk_size
            num_chunks = math.ceil(data_size / chunk_size)

            self.logger.debug(
                f"[AZURE CHUNKED] Uploading {data_size/1024/1024:.2f}MB in {num_chunks} chunks")

            block_list = []

            for i in range(num_chunks):
                start_idx = i * chunk_size
                end_idx = min((i + 1) * chunk_size, data_size)
                chunk = data[start_idx:end_idx]

                if not chunk:
                    break

                # Generate block ID (must be base64 encoded)
                block_id = f"block{i:05d}".encode('utf-8')

                # Upload chunk with timeout
                blob_client.stage_block(block_id=block_id, data=chunk, timeout=60)
                block_list.append(BlobBlock(block_id=block_id))

                # Log progress for large uploads
                if num_chunks > 1 and (i + 1) % max(1, num_chunks // 10) == 0:
                    progress = (i + 1) / num_chunks * 100
                    self.logger.debug(
                        f"[AZURE CHUNKED] Upload progress: {progress:.1f}% ({i+1}/{num_chunks} chunks)")

            # Commit all blocks
            blob_client.commit_block_list(block_list)
            self.stats['chunked_uploads'] += 1
            self.performance['total_chunks_uploaded'] += num_chunks

            if num_chunks > 1:
                self.performance['avg_chunks_per_upload'] = (
                    self.performance['avg_chunks_per_upload'] * (self.stats['chunked_uploads'] - 1) + num_chunks
                ) / self.stats['chunked_uploads']

            return True

        except Exception as e:
            self.logger.error(
                f"[AZURE CHUNKED ERROR] Failed to upload in chunks: {str(e)}")
            return False

    def upload_to_azure(self, file_path, device_id, operation_id, file_type):
        """
        Upload a file to Azure Blob Storage and generate a SAS URL.
        """
        if not self.azure_initialized:
            self.logger.warning("[AZURE] Azure not initialized, skipping upload")
            return None

        if not os.path.exists(file_path):
            self.logger.error(f"[AZURE] File not found: {file_path}")
            return None

        try:
            file_name = os.path.basename(file_path)
            file_size = os.path.getsize(file_path)

            if file_type == "video":
                blob_name = f"{device_id}/{operation_id}/videos/{file_name}"
            elif file_type in ["before", "after"]:
                blob_name = f"{device_id}/{operation_id}/images/{file_type}_{file_name}"
            else:
                blob_name = f"{device_id}/{operation_id}/{file_type}/{file_name}"

            self.logger.info(f"[AZURE] Uploading {file_type} file ({file_size/1024/1024:.2f}MB) to: {blob_name}")
            
            self._update_status(operation_id, UploadStatus.AZURE_UPLOADING,
                                f"Uploading {file_type} to Azure",
                                {'blob_name': blob_name, 'file_size': file_size})

            upload_start = time.time()

            # Detect content type
            content_type, _ = mimetypes.guess_type(file_path)
            if not content_type:
                content_type = 'video/mp4' if file_type == "video" else 'image/jpeg' if file_type in ["before", "after"] else 'application/octet-stream'

            blob_client = self.container_client.get_blob_client(blob_name)

            if file_type == "video" and file_size > self.video_chunk_threshold:
                success = self._upload_file_in_chunks_streaming(file_path, blob_name, content_type)
                if not success: raise Exception("Streaming chunked upload failed")
            elif file_size <= self.max_simple_upload_size:
                with open(file_path, "rb") as data:
                    blob_client.upload_blob(
                        data, overwrite=True,
                        content_settings=ContentSettings(content_type=content_type),
                        timeout=120
                    )
                self.stats['direct_uploads'] += 1
            else:
                with open(file_path, "rb") as file:
                    file_data = file.read()
                success = self._upload_data_in_chunks(file_data, blob_name, content_type)
                if not success: raise Exception("Chunked upload failed")

            upload_time = time.time() - upload_start
            self.stats['azure_uploads'] += 1

            # Generate base URL (without SAS token) as requested by user
            try:
                base_url = f"https://{self.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}"
            except AttributeError:
                # Fallback to generating SAS then stripping token
                sas_url = self.generate_sas_url(blob_name)
                if sas_url:
                    base_url = sas_url.split('?')[0]
                else:
                    base_url = f"https://account.blob.core.windows.net/container/{blob_name}"

            self.logger.info(f"[AZURE] Uploaded {file_type} in {upload_time:.2f}s. URL: {base_url}")
            return base_url

        except Exception as e:
            self.logger.error(f"[AZURE UPLOAD ERROR] {str(e)}")
            self._update_status(operation_id, UploadStatus.FAILED, f"Azure upload failed: {str(e)}")
            self.stats['azure_failed'] += 1
            return None

    def upload_images_to_azure(self, operation_data):
        """
        Upload both before and after images to Azure
        """
        device_id = operation_data.get('device_id', 'UNKNOWN')
        operation_id = operation_data.get('operation_id', 'unknown')

        before_url = None
        after_url = None

        self._update_status(
            operation_id,
            UploadStatus.AZURE_UPLOADING,
            "Starting Azure image uploads")

        # Upload before image
        before_path = operation_data.get('before_path')
        if before_path and os.path.exists(before_path):
            before_url = self.upload_to_azure(
                before_path, device_id, operation_id, "before")
        else:
            self.logger.warning(
                f"[AZURE] Before image not found: {before_path}")

        # Upload after image
        after_path = operation_data.get('after_path')
        if after_path and os.path.exists(after_path):
            after_url = self.upload_to_azure(
                after_path, device_id, operation_id, "after")
        else:
            self.logger.warning(f"[AZURE] After image not found: {after_path}")

        return before_url, after_url

    def upload_video_to_azure(self, operation_data):
        """
        Upload video file to Azure with special handling for large files
        """
        device_id = operation_data.get('device_id', 'UNKNOWN')
        operation_id = operation_data.get('operation_id', 'unknown')
        video_path = operation_data.get('video_path')

        if video_path and os.path.exists(video_path):
            video_size = os.path.getsize(video_path)
            self.logger.info(f"[VIDEO UPLOAD] Video size: {video_size/1024/1024:.2f}MB")
            
            # Update status with video size
            self._update_status(
                operation_id,
                UploadStatus.AZURE_UPLOADING,
                f"Uploading video ({video_size/1024/1024:.1f}MB) to Azure",
                {'video_size_mb': video_size/1024/1024}
            )
            
            return self.upload_to_azure(video_path, device_id, operation_id, "video")
        else:
            self.logger.warning(f"[AZURE] Video not found: {video_path}")
            return None

    def generate_sas_url(self, blob_name, expiration_hours=24):
        """Generate SAS URL for direct download (if needed for sharing)"""
        try:
            if not self.azure_initialized:
                self.logger.warning(
                    "[AZURE] Cannot generate SAS URL: Azure not initialized")
                return None

            from azure.storage.blob import generate_blob_sas, BlobSasPermissions

            # Extract account name from connection string
            account_name = self.blob_service_client.account_name

            # Get account key from connection string
            account_key = None
            conn_str = self.blob_service_client.credential.account_key
            if conn_str:
                account_key = conn_str

            if not account_key:
                self.logger.error(
                    "[AZURE] Cannot generate SAS URL: Account key not available")
                return None

            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=self.container_client.container_name,
                blob_name=blob_name,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(hours=expiration_hours)
            )

            sas_url = f"https://{account_name}.blob.core.windows.net/{self.container_client.container_name}/{blob_name}?{sas_token}"
            return sas_url

        except Exception as e:
            self.logger.error(
                f"[AZURE SAS ERROR] Failed to generate SAS URL: {str(e)}")
            return None

    def start_upload_thread(self):
        """Start the background upload thread"""
        if not self.is_running:
            self.is_running = True
            self.upload_thread = threading.Thread(
                target=self._process_upload_queue, daemon=True)
            self.upload_thread.start()
            self.logger.info("[UPLOADER] Upload thread started")

    def stop_upload_thread(self):
        """Stop the background upload thread"""
        self.is_running = False
        if self.upload_thread:
            self.upload_thread.join(timeout=5)
            self.logger.info("[UPLOADER] Upload thread stopped")

    def queue_operation(self, **kwargs):
        """
        Queue an operation for upload
        
        Args:
            video_path: Path to video file (for pipe inspection)
            device_id: Device identifier (from config)
            operation_id: Unique operation ID
            manhole_id: Manhole identifier
            config: Configuration dict with Azure credentials
            operation_type: 'pipe_inspection' for videos
        """
        try:
            operation_id = kwargs.get('operation_id', 'unknown')
            config = kwargs.get('config', {})
            manhole_id = kwargs.get('manhole_id', 'unknown')
            operation_type = kwargs.get('operation_type', 'manhole_cleaning')

            self.logger.info(
                f"[UPLOAD] Queueing {operation_type} operation {operation_id}")

            # Check file sizes for large file handling
            if operation_type == 'pipe_inspection' and kwargs.get('video_path'):
                video_path = kwargs['video_path']
                if os.path.exists(video_path):
                    video_size = os.path.getsize(video_path)
                    self.logger.info(f"[UPLOAD] Video size: {video_size/1024/1024:.2f}MB")
                    
                    # Warn about large videos
                    if video_size > 50 * 1024 * 1024:  # 50MB
                        self.logger.warning(f"[UPLOAD] Large video detected ({video_size/1024/1024:.1f}MB). Using optimized upload.")
                else:
                    self.logger.warning(f"[UPLOAD] Video path doesn't exist: {video_path}")

            # Update stats based on operation type
            if operation_type == 'pipe_inspection':
                self.stats['pipe_inspection'] += 1
            else:
                self.stats['manhole_cleaning'] += 1

            # Validate manhole_id
            manhole_id = str(manhole_id).strip() if manhole_id else "UNKNOWN"
            
            if not manhole_id or manhole_id.lower() in ['unknown', 'null', 'none']:
                self.logger.warning(f"[UPLOAD] Invalid manhole_id: '{manhole_id}'")
                location = kwargs.get('location', {})
                lat = location.get('latitude', 0)
                lon = location.get('longitude', 0)
                if lat != 0 and lon != 0:
                    manhole_id = f"GPS_{int(lat*1000000)}_{int(lon*1000000)}"
                else:
                    manhole_id = f"MH_{datetime.now().strftime('%m%d%H%M%S')}"
                self.logger.info(f"[UPLOAD] Generated manhole_id: {manhole_id}")

            # 🎯 INITIALIZE AZURE HERE (when first video is queued)
            if config and not self.azure_initialized:
                azure_success = self.init_azure(config)
                if azure_success:
                    self.logger.info("[UPLOAD] Azure initialized successfully")
                else:
                    self.logger.warning(
                        "[UPLOAD] Azure initialization failed, will use local paths")

            # Prepare operation data dictionary
            operation_data = {
                'operation_id': operation_id,
                'operation_type': operation_type,
                'before_path': kwargs.get('before_path'),
                'after_path': kwargs.get('after_path'),
                'video_path': kwargs.get('video_path'),
                'before_depth': kwargs.get('before_depth'),
                'after_depth': kwargs.get('after_depth'),
                'config': config,
                'location': kwargs.get('location', {}),
                'start_time': kwargs.get('start_time'),
                'end_time': kwargs.get('end_time'),
                'duration_seconds': kwargs.get('duration_seconds', 0),
                'manhole_id': manhole_id,
                'area': os.getenv('area') or config.get('area', 'UNKNOWN'),
                'division': os.getenv('division') or config.get('division', 'UNKNOWN'),
                'district': os.getenv('district') or config.get('district', 'UNKNOWN'),
                'device_id': os.getenv('device_id') or config.get('device_id', 'UNKNOWN'),
                'pipe_inspection_starttime': kwargs.get('pipe_inspection_starttime'),
                'pipe_inspection_endtime': kwargs.get('pipe_inspection_endtime'),
                'pipe_inspection_operationtime': kwargs.get('pipe_inspection_operationtime', 0),
                'gas_data': kwargs.get('gas_data', {}),
                'queue_time': datetime.now(),
                'azure_success': False,
                'api_success': False
            }

            # Log the data being queued
            self.logger.info(f"[UPLOAD] Operation data queued:")
            self.logger.info(f"  - operation_type: {operation_data['operation_type']}")
            self.logger.info(f"  - manhole_id: '{operation_data['manhole_id']}'")
            self.logger.info(f"  - device_id: '{operation_data['device_id']}'")
            self.logger.info(f"  - operation_id: '{operation_data['operation_id']}'")
            self.logger.info(f"  - azure_initialized: {self.azure_initialized}")

            # Add to operation queue
            self.operation_queue.put(operation_data)
            self.stats['queued'] += 1
            self.stats['total_attempted'] += 1

            self._update_status(
                operation_id,
                UploadStatus.QUEUED,
                f"{operation_type.replace('_', ' ').title()} operation queued for upload",
                {
                    'queue_size': self.operation_queue.qsize(),
                    'manhole_id': manhole_id,
                    'operation_type': operation_type})

            self.logger.info(
                f"[UPLOAD] Operation {operation_id} added to queue. Queue size: {self.operation_queue.qsize()}")
            return operation_id

        except Exception as e:
            self.logger.error(
                f"[UPLOAD QUEUE ERROR] Failed to queue operation: {str(e)}")
            self.logger.error(
                f"[UPLOAD QUEUE ERROR] Traceback: {traceback.format_exc()}")
            return None

    def _prepare_form_data(self, operation_data):
        """
        Prepare JSON data according to API requirements based on operation type
        """
        try:
            operation_id = operation_data.get('operation_id', 'unknown')
            operation_type = operation_data.get(
                'operation_type', 'manhole_cleaning')

            self._update_status(
                operation_id,
                UploadStatus.PROCESSING,
                "Preparing JSON data")

            if operation_type == 'pipe_inspection':
                return self._prepare_pipe_inspection_form_data(operation_data)
            else:
                return self._prepare_manhole_cleaning_form_data(operation_data)

        except Exception as e:
            self.logger.error(
                f"[FORM DATA ERROR] Failed to prepare JSON data: {str(e)}")
            self.logger.error(
                f"[FORM DATA ERROR] Traceback: {traceback.format_exc()}")
            return {}

    def _prepare_manhole_cleaning_form_data(self, operation_data):
        """Prepare JSON data for manhole cleaning operations"""
        try:
            operation_id = operation_data.get('operation_id', 'unknown')

            self._update_status(
                operation_id,
                UploadStatus.PROCESSING,
                "Preparing manhole cleaning data")

            # Upload images to Azure FIRST
            before_url = ""
            after_url = ""

            if self.azure_initialized:
                self._update_status(
                    operation_id,
                    UploadStatus.AZURE_UPLOADING,
                    "Uploading images to Azure")
                before_url, after_url = self.upload_images_to_azure(
                    operation_data)

            # If Azure upload failed or not configured, use local paths as fallback
            if not before_url and operation_data.get('before_path'):
                before_url = f"file://{os.path.abspath(operation_data['before_path'])}"
                self.logger.warning(
                    f"[AZURE] Using local path for before image: {before_url}")

            if not after_url and operation_data.get('after_path'):
                after_url = f"file://{os.path.abspath(operation_data['after_path'])}"
                self.logger.warning(
                    f"[AZURE] Using local path for after image: {after_url}")

            # Get location data
            location_data = operation_data.get('location', {})
            latitude = location_data.get('latitude', 0.0)
            longitude = location_data.get('longitude', 0.0)
            gps_fix = location_data.get('gps_fix', False)

            # Prepare timestamps
            start_time = operation_data.get('start_time')
            end_time = operation_data.get('end_time')

            # Convert datetime objects to ISO format strings
            if start_time and hasattr(start_time, 'isoformat'):
                start_time_str = start_time.isoformat()
            else:
                start_time_str = str(start_time) if start_time else ''

            if end_time and hasattr(end_time, 'isoformat'):
                end_time_str = end_time.isoformat()
            else:
                end_time_str = str(end_time) if end_time else ''

            # Get manhole_id
            manhole_id = operation_data.get('manhole_id', '').strip()
            if not manhole_id:
                manhole_id = f"MH_{datetime.now().strftime('%m%d%H%M%S')}"
            # Prepare JSON data for manhole cleaning
            json_data = {
                "manhole_id": manhole_id,
                "device_id": operation_data.get('device_id', 'UNKNOWN'),
                "operation_type": "manhole_cleaning",
                "op_status": "Completed",
                "before_op_image_url": before_url if before_url else "N/A",
                "after_op_image_url": after_url if after_url else "N/A",
                "op_video_url": "",  # Null for manhole cleaning
                # FLAT FIELDS (not nested in location JSON string)
                "op_latitude": float(latitude),
                "op_longitude": float(longitude),
                "op_district": operation_data.get('district', 'UNKNOWN'),
                "op_division": operation_data.get('division', 'UNKNOWN'),
                "op_section": operation_data.get('area', 'UNKNOWN'),
                "op_duration_seconds": int(operation_data.get('duration_seconds', 0)),
                "op_depth_before": operation_data.get('before_depth'),
                "op_depth_after": operation_data.get('after_depth'),
                "op_start_time": start_time_str,
                "op_end_time": end_time_str,
                "op_clog_depth": (operation_data.get('before_depth') or 0.0) - (operation_data.get('after_depth') or 0.0),
                "op_gas_data_raw": operation_data.get('gas_data', {}),
                "op_gas_status": ""
            }

            # Log the JSON data being sent (for debugging)
            self.logger.info("=" * 80)
            self.logger.info(f"[MANHOLE CLEANING] COMPLETE JSON DATA FOR DATABASE {operation_id}:")
            self.logger.info(json.dumps(json_data, indent=2, cls=DateTimeEncoder))
            self.logger.info("=" * 80)
            
            self._update_status(
                operation_id,
                UploadStatus.PROCESSING,
                "Manhole cleaning data prepared successfully")

            return json_data

        except Exception as e:
            self.logger.error(f"[MANHOLE CLEANING FORM DATA ERROR] {str(e)}")
            return {}

    def _prepare_pipe_inspection_form_data(self, operation_data):
        """Prepare JSON data according to API requirements for pipe inspection"""
        try:
            operation_id = operation_data.get('operation_id', 'unknown')

            self._update_status(
                operation_id,
                UploadStatus.PROCESSING,
                "Preparing pipe inspection data")

            # Upload video to Azure
            video_url = ""

            if self.azure_initialized:
                self._update_status(
                    operation_id,
                    UploadStatus.AZURE_UPLOADING,
                    "Uploading video to Azure")
                video_url = self.upload_video_to_azure(operation_data)
                # Track Azure success in operation data
                operation_data['azure_success'] = bool(video_url and video_url.startswith('http'))

            # If Azure upload failed or not configured, use local path as fallback
            if not video_url and operation_data.get('video_path'):
                video_url = f"file://{os.path.abspath(operation_data['video_path'])}"
                self.logger.warning(
                    f"[AZURE] Using local path for video: {video_url}")

            # Get location data
            location_data = operation_data.get('location', {})
            latitude = location_data.get('latitude', 0.0)
            longitude = location_data.get('longitude', 0.0)
            gps_fix = location_data.get('gps_fix', False)

            # Prepare timestamps for pipe inspection
            pipe_start_time = operation_data.get('pipe_inspection_starttime')
            pipe_end_time = operation_data.get('pipe_inspection_endtime')

            # Convert datetime objects to ISO format strings
            if pipe_start_time and hasattr(pipe_start_time, 'isoformat'):
                pipe_start_time_str = pipe_start_time.isoformat()
            else:
                pipe_start_time_str = str(
                    pipe_start_time) if pipe_start_time else ''

            if pipe_end_time and hasattr(pipe_end_time, 'isoformat'):
                pipe_end_time_str = pipe_end_time.isoformat()
            else:
                pipe_end_time_str = str(pipe_end_time) if pipe_end_time else ''

            # Get manhole_id
            manhole_id = operation_data.get('manhole_id', '').strip()
            if not manhole_id:
                manhole_id = f"PIPE_{datetime.now().strftime('%m%d%H%M%S')}"

            # Prepare JSON data for pipe inspection
            json_data = {
                "manhole_id": manhole_id,
                "device_id": operation_data.get('device_id', 'UNKNOWN'),
                "operation_id": operation_data.get('operation_id', 'unknown_operation'),
                "operation_type": "pipe_inspection",
                "before_image_url": "N/A",  # Null for pipe inspection
                "after_image_url": "N/A",   # Null for pipe inspection
                "video_url": video_url if video_url else "N/A",
                # FLAT FIELDS (not nested in location JSON string)
                "latitude": float(latitude),
                "longitude": float(longitude),
                "gps_fix": bool(gps_fix),
                "updated_at": datetime.now().isoformat(),
                "district": operation_data.get('district', 'UNKNOWN'),
                "division": operation_data.get('division', 'UNKNOWN'),
                "area": operation_data.get('area', 'UNKNOWN'),
                "duration_seconds": int(operation_data.get('duration_seconds', 0)),
                "start_time": pipe_start_time_str if pipe_start_time_str else '',
                "end_time": pipe_end_time_str if pipe_end_time_str else '',
                "pipe_inspection_starttime": pipe_start_time_str,
                "pipe_inspection_endtime": pipe_end_time_str,
                "pipe_inspection_operationtime": int(operation_data.get('pipe_inspection_operationtime', 0))
            }

            # Log the JSON data being sent (for debugging)
            self.logger.info("=" * 80)
            self.logger.info(f"[PIPE INSPECTION] COMPLETE JSON DATA FOR DATABASE {operation_id}:")
            self.logger.info(json.dumps(json_data, indent=2, cls=DateTimeEncoder))
            self.logger.info("=" * 80)

            self._update_status(
                operation_id,
                UploadStatus.PROCESSING,
                "Pipe inspection data prepared successfully")

            return json_data

        except Exception as e:
            self.logger.error(f"[PIPE INSPECTION FORM DATA ERROR] {str(e)}")
            return {}

    def _upload_with_retry(self, operation_data):
        """
        Attempt to upload operation data with retries
        Returns tuple: (success, azure_success, api_success)
        """
        operation_id = operation_data.get('operation_id', 'unknown')
        manhole_id = operation_data.get('manhole_id', 'unknown')
        operation_type = operation_data.get(
            'operation_type', 'manhole_cleaning')
        
        # Track partial successes
        azure_success = operation_data.get('azure_success', False)
        api_success = operation_data.get('api_success', False)

        for attempt in range(self.max_retries):
            try:
                self.logger.info(
                    f"[UPLOAD] Attempt {attempt + 1}/{self.max_retries} for {operation_type} operation {operation_id}")
                self.logger.info(f"[UPLOAD] Using manhole ID: {manhole_id}")

                self._update_status(
                    operation_id,
                    UploadStatus.RETRYING if attempt > 0 else UploadStatus.PROCESSING,
                    f"Upload attempt {attempt + 1}/{self.max_retries}")

                # Prepare the payload according to API requirements (JSON DATA)
                json_data = self._prepare_form_data(operation_data)

                if not json_data:
                    self.logger.error(
                        f"[UPLOAD] Failed to prepare JSON data for {operation_id}")
                    self.stats['validation_errors'] += 1
                    break

                # DEBUG: Log operation type specific details
                if operation_type == 'pipe_inspection':
                    self.logger.info(
                        f"[PIPE INSPECTION DEBUG] JSON data for {operation_id}:")
                    self.logger.info(
                        f"  - operation_type: {json_data.get('operation_type')}")
                    self.logger.info(
                        f"  - pipe_inspection_operationtime: {json_data.get('pipe_inspection_operationtime')}")
                    # Log video URL status
                    video_url = json_data.get('video_url', 'N/A')
                    if video_url.startswith('http'):
                        self.logger.info(f"  - video_url: Azure URL provided")
                        azure_success = True
                    elif video_url.startswith('file://'):
                        self.logger.warning(f"  - video_url: Local file path (Azure upload may have failed)")
                    else:
                        self.logger.warning(f"  - video_url: {video_url}")
                # Make the API request with JSON data
                self._update_status(
                    operation_id,
                    UploadStatus.API_SENDING,
                    "Sending data to API")

                upload_start = time.time()
                
                # Send JSON data to API
                # First, let's log exactly what we're sending
                self.logger.info("=" * 80)
                self.logger.info(f"[API REQUEST] Sending JSON data to {self.api_url}")
                self.logger.info(f"[API REQUEST] JSON keys: {list(json_data.keys())}")
                self.logger.info(f"[API REQUEST] Operation Type: {operation_type}")
                self.logger.info(f"[API REQUEST] Manhole ID: {manhole_id}")
                self.logger.info(f"[API REQUEST] Device ID: {json_data.get('device_id', 'UNKNOWN')}")
                self.logger.info(f"[API REQUEST] Video URL type: {'Azure URL' if 'blob.core.windows.net' in str(json_data.get('video_url', '')) else 'Local/Other'}")
                self.logger.info(f"[API REQUEST] Image URLs: Before={json_data.get('before_image_url', 'N/A')[:100]}, After={json_data.get('after_image_url', 'N/A')[:100]}")
                self.logger.info("=" * 80)

                try:
                    headers = {
                        'User-Agent': 'SmartWasteDashboard/1.0',
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    }
                    
                    json_str = json.dumps(json_data, cls=DateTimeEncoder)
                    self.logger.info(f"[API REQUEST] Sending JSON payload ({len(json_str)} bytes) to {self.api_url}")
                    
                    response = requests.post(
                        self.api_url,
                        data=json_str,
                        headers=headers,
                        timeout=180 if operation_type == 'pipe_inspection' else 60
                    )
                except Exception as api_err:
                    self.logger.error(f"[API REQUEST ERROR] {api_err}")
                    raise api_err
                
                upload_time = time.time() - upload_start

                self.logger.info(
                    f"[UPLOAD] API response time: {upload_time:.2f}s")
                self.logger.info(f"[UPLOAD] API response status: {response.status_code}")
                self.logger.info(f"[UPLOAD] API response first 500 chars: {response.text[:500]}")

                if response.status_code in [200, 201]:
                    self.logger.info(
                        f"[UPLOAD] ✅ API responded with status {response.status_code} for {operation_type} operation {operation_id}")
                    api_success = True

                    try:
                        response_data = response.json()
                        self.logger.info(
                            f"[UPLOAD] API Response JSON: {json.dumps(response_data, indent=2)}")
                        
                        # Update operation data with success status
                        operation_data['api_success'] = api_success
                        operation_data['azure_success'] = azure_success
                        
                        if azure_success and not operation_data.get('api_success_before_retry', False):
                            self._update_status(
                                operation_id,
                                UploadStatus.PARTIAL_SUCCESS,
                                f"Partial success: Azure upload succeeded, API now succeeded",
                                {
                                    'response': response_data,
                                    'upload_time': upload_time,
                                    'azure_success': azure_success,
                                    'api_success': api_success})
                            self.stats['partial_successes'] += 1
                        else:
                            self._update_status(
                                operation_id,
                                UploadStatus.SUCCESS,
                                f"{operation_type.replace('_', ' ').title()} upload successful (HTTP {response.status_code})",
                                {
                                    'response': response_data,
                                    'upload_time': upload_time,
                                    'azure_success': azure_success,
                                    'api_success': api_success})
                    except json.JSONDecodeError:
                        self.logger.info(
                            f"[UPLOAD] Response text: {response.text[:200]}")
                        
                        # Update operation data with success status
                        operation_data['api_success'] = api_success
                        operation_data['azure_success'] = azure_success
                        
                        if azure_success and not operation_data.get('api_success_before_retry', False):
                            self._update_status(operation_id, UploadStatus.PARTIAL_SUCCESS,
                                                f"Partial success: Azure upload succeeded, API now succeeded",
                                                {'response_text': response.text[:200], 
                                                 'upload_time': upload_time,
                                                 'azure_success': azure_success,
                                                 'api_success': api_success})
                            self.stats['partial_successes'] += 1
                        else:
                            self._update_status(operation_id, UploadStatus.SUCCESS,
                                                f"{operation_type.replace('_', ' ').title()} ================================================================================upload successful (HTTP {response.status_code})",
                                                {'response_text': response.text[:200], 
                                                 'upload_time': upload_time,
                                                 'azure_success': azure_success,
                                                 'api_success': api_success})

                    # Update performance metrics
                    self._update_performance_metrics(upload_time)

                    return True, azure_success, api_success

                else:
                    self.logger.warning(
                        f"[UPLOAD] Attempt {attempt + 1} failed with status {response.status_code}")
                    self.logger.warning(
                        f"[UPLOAD] Response: {response.text[:500]}")

                    error_details = {
                        'status_code': response.status_code,
                        'response': response.text[:500],
                        'attempt': attempt + 1,
                        'upload_time': upload_time,
                        'operation_type': operation_type,
                        'azure_success': azure_success,
                        'api_success': api_success
                    }

                    # Check if we have partial success (Azure succeeded but API failing)
                    if azure_success and not api_success:
                        # Mark that API needs retry but Azure succeeded
                        operation_data['api_success_before_retry'] = False
                        self._update_status(
                            operation_id,
                            UploadStatus.PARTIAL_SUCCESS,
                            f"Partial success: Azure upload succeeded, API failed (HTTP {response.status_code})",
                            error_details)
                        self.stats['partial_successes'] += 1
                        # Don't return yet, try to retry API
                    else:
                        self._update_status(
                            operation_id,
                            UploadStatus.FAILED,
                            f"HTTP {response.status_code}: {response.text[:100]}",
                            error_details)

                    if response.status_code >= 400 and response.status_code < 500:
                        self.stats['api_errors'] += 1
                    else:
                        self.stats['failed'] += 1

                    if attempt < self.max_retries - 1:
                        retry_delay = self.retry_delay * (attempt + 1)
                        self.logger.info(
                            f"[UPLOAD] Retrying in {retry_delay} seconds...")
                        self._update_status(
                            operation_id,
                            UploadStatus.RETRYING,
                            f"Retrying in {retry_delay}s (attempt {attempt + 2})")
                        time.sleep(retry_delay)

            except requests.exceptions.Timeout:
                self.logger.warning(
                    f"[UPLOAD TIMEOUT] Attempt {attempt + 1} timed out for operation {operation_id}")
                
                # Check if we have partial success (Azure succeeded but API timed out)
                if azure_success and not api_success:
                    operation_data['api_success_before_retry'] = False
                    self._update_status(
                        operation_id,
                        UploadStatus.PARTIAL_SUCCESS,
                        f"Partial success: Azure upload succeeded, API timed out")
                    self.stats['partial_successes'] += 1
                    return False, azure_success, api_success
                else:
                    self._update_status(
                        operation_id,
                        UploadStatus.FAILED,
                        f"Timeout on attempt {attempt + 1}")
                    self.stats['timeouts'] += 1

                if attempt < self.max_retries - 1:
                    retry_delay = self.retry_delay * (attempt + 1)
                    time.sleep(retry_delay)

            except requests.exceptions.ConnectionError:
                self.logger.error(
                    f"[UPLOAD CONNECTION ERROR] Cannot connect to API for operation {operation_id}")
                
                # Check if we have partial success (Azure succeeded but API connection error)
                if azure_success and not api_success:
                    operation_data['api_success_before_retry'] = False
                    self._update_status(
                        operation_id,
                        UploadStatus.PARTIAL_SUCCESS,
                        f"Partial success: Azure upload succeeded, API connection error")
                    self.stats['partial_successes'] += 1
                    return False, azure_success, api_success
                else:
                    self._update_status(
                        operation_id,
                        UploadStatus.FAILED,
                        "Connection error")
                    self.stats['connection_errors'] += 1

                if attempt < self.max_retries - 1:
                    retry_delay = self.retry_delay * (attempt + 1)
                    time.sleep(retry_delay)

            except Exception as e:
                self.logger.error(
                    f"[UPLOAD ERROR] Unexpected error on attempt {attempt + 1}: {str(e)}")
                self.logger.error(
                    f"[UPLOAD ERROR] Traceback: {traceback.format_exc()}")
                
                # Check if we have partial success
                if azure_success and not api_success:
                    operation_data['api_success_before_retry'] = False
                    self._update_status(
                        operation_id,
                        UploadStatus.PARTIAL_SUCCESS,
                        f"Partial success: Azure upload succeeded, API error: {str(e)[:100]}")
                    self.stats['partial_successes'] += 1
                    return False, azure_success, api_success
                else:
                    self._update_status(
                        operation_id,
                        UploadStatus.FAILED,
                        f"Unexpected error: {str(e)[:100]}")

                if attempt < self.max_retries - 1:
                    retry_delay = self.retry_delay * (attempt + 1)
                    time.sleep(retry_delay)

        return False, azure_success, api_success

    def _update_performance_metrics(self, upload_time):
        """Update performance tracking metrics"""
        self.performance['total_upload_time'] += upload_time
        self.performance['upload_count'] += 1
        self.performance['avg_upload_time'] = self.performance['total_upload_time'] / \
            self.performance['upload_count']

        if upload_time < self.performance['fastest_upload']:
            self.performance['fastest_upload'] = upload_time

        if upload_time > self.performance['slowest_upload']:
            self.performance['slowest_upload'] = upload_time

    def _process_upload_queue(self):
        """Background thread to process the upload queue"""
        self.logger.info("[UPLOADER] Queue processor started")

        while self.is_running:
            try:
                # Wait for operations in the queue (with timeout to check is_running)
                try:
                    operation_data = self.operation_queue.get(timeout=1)
                except queue.Empty:
                    continue

                # Process the operation
                operation_id = operation_data.get('operation_id', 'unknown')
                manhole_id = operation_data.get('manhole_id', 'unknown')
                operation_type = operation_data.get(
                    'operation_type', 'manhole_cleaning')

                self.logger.info(
                    f"[UPLOAD PROCESSOR] Processing {operation_type} operation {operation_id}")
                self.logger.info(
                    f"[UPLOAD PROCESSOR] Manhole ID: {manhole_id}")
                self.logger.info(
                    f"[UPLOAD PROCESSOR] Queue size remaining: {self.operation_queue.qsize()}")

                self._update_status(operation_id, UploadStatus.PROCESSING,
                                    f"Starting {operation_type} upload processing",
                                    {'queue_position': self.operation_queue.qsize() + 1,
                                     'operation_type': operation_type})

                upload_start = datetime.now()
                success, azure_success, api_success = self._upload_with_retry(operation_data)
                total_time = (datetime.now() - upload_start).total_seconds()

                # Update operation data with final status
                operation_data['azure_success'] = azure_success
                operation_data['api_success'] = api_success

                if success:
                    self.stats['successful'] += 1
                    self.logger.info(
                        f"[UPLOAD PROCESSOR] ✅ {operation_type.title()} operation {operation_id} uploaded successfully in {total_time:.2f}s")

                    # Clean up files after successful upload (only if both Azure and API succeeded)
                    try:
                        if operation_type == 'manhole_cleaning':
                            before_path = operation_data.get('before_path')
                            after_path = operation_data.get('after_path')

                            if before_path and os.path.exists(before_path) and azure_success and api_success:
                                os.remove(before_path)
                                self.logger.info(
                                    f"[UPLOAD] Removed before image: {before_path}")

                            if after_path and os.path.exists(after_path) and azure_success and api_success:
                                os.remove(after_path)
                                self.logger.info(
                                    f"[UPLOAD] Removed after image: {after_path}")

                        elif operation_type == 'pipe_inspection':
                            video_path = operation_data.get('video_path')
                            if video_path and os.path.exists(video_path):
                                # Check if both Azure and API succeeded before deleting
                                if azure_success and api_success:
                                    os.remove(video_path)
                                    self.logger.info(
                                        f"[UPLOAD] Removed video file: {video_path}")
                                elif azure_success and not api_success:
                                    self.logger.warning(
                                        f"[UPLOAD] Azure succeeded but API failed, keeping video file: {video_path}")
                                else:
                                    self.logger.warning(
                                        f"[UPLOAD] Azure not initialized or failed, keeping video file: {video_path}")

                    except Exception as e:
                        self.logger.error(
                            f"[UPLOAD CLEANUP ERROR] Failed to remove files: {str(e)}")

                elif azure_success and not api_success:
                    # Partial success: Azure succeeded but API failed
                    self.logger.warning(
                        f"[UPLOAD PROCESSOR] ⚠️ Partial success: Azure upload succeeded but API failed for operation {operation_id}")
                    # Don't count as complete failure, but save for API retry
                    self._save_for_retry(operation_data)
                elif not azure_success and api_success:
                    # Partial success: API succeeded but Azure failed
                    self.logger.warning(
                        f"[UPLOAD PROCESSOR] ⚠️ Partial success: API succeeded but Azure failed for operation {operation_id}")
                    self.stats['failed'] += 1
                    self._save_for_retry(operation_data)
                else:
                    # Complete failure
                    self.stats['failed'] += 1
                    self.logger.error(
                        f"[UPLOAD PROCESSOR] ❌ {operation_type.title()} operation {operation_id} failed after {total_time:.2f}s")
                    self._save_for_retry(operation_data)

                # Add to history
                history_entry = {
                    'operation_id': operation_id,
                    'manhole_id': manhole_id,
                    'operation_type': operation_type,
                    'success': success,
                    'azure_success': azure_success,
                    'api_success': api_success,
                    'duration': total_time,
                    'timestamp': datetime.now(),
                    'azure_used': self.azure_initialized,
                    'chunked_upload': operation_type == 'pipe_inspection' and self.azure_initialized
                }
                self.operation_history.append(history_entry)

                # Trim history
                if len(self.operation_history) > self.max_history:
                    self.operation_history = self.operation_history[-self.max_history:]

                # Remove from in-progress tracking after delay
                if operation_id in self.in_progress_operations:
                    def remove_in_progress(op_id):
                        if op_id in self.in_progress_operations:
                            del self.in_progress_operations[op_id]
                    threading.Timer(
                        10.0, remove_in_progress, args=[operation_id]).start()

                # Mark task as done
                self.operation_queue.task_done()

            except Exception as e:
                self.logger.error(f"[UPLOAD PROCESSOR ERROR] {str(e)}")
                self.logger.error(
                    f"[UPLOAD PROCESSOR ERROR] Traceback: {traceback.format_exc()}")
                time.sleep(1)

        self.logger.info("[UPLOADER] Queue processor stopped")

    def _save_for_retry(self, operation_data):
        """
        Save failed operation for later retry
        Only save operations that need retry
        """
        try:
            operation_id = operation_data.get('operation_id', 'unknown')
            azure_success = operation_data.get('azure_success', False)
            api_success = operation_data.get('api_success', False)
            
            # Determine what needs retry
            needs_azure_retry = not azure_success
            needs_api_retry = not api_success
            
            if needs_azure_retry or needs_api_retry:
                # Update operation data
                operation_data['needs_azure_retry'] = needs_azure_retry
                operation_data['needs_api_retry'] = needs_api_retry
                
                # Add to failed queue
                self.failed_queue.put(operation_data)

                # Also save to file for persistence
                self._save_to_file(operation_data)

                self.logger.warning(
                    f"[UPLOAD] Operation {operation_id} saved for retry (Azure retry: {needs_azure_retry}, API retry: {needs_api_retry})")
                
                if azure_success and not api_success:
                    self._update_status(
                        operation_id,
                        UploadStatus.PARTIAL_SUCCESS,
                        f"Saved for API retry (Azure succeeded)")
                elif not azure_success and api_success:
                    self._update_status(
                        operation_id,
                        UploadStatus.PARTIAL_SUCCESS,
                        f"Saved for Azure retry (API succeeded)")
                else:
                    self._update_status(
                        operation_id,
                        UploadStatus.FAILED,
                        f"Saved for retry (both failed)")
            else:
                self.logger.info(
                    f"[UPLOAD] Operation {operation_id} not saved for retry (both succeeded)")

        except Exception as e:
            self.logger.error(
                f"[RETRY SAVE ERROR] Failed to save operation for retry: {str(e)}")

    def _save_to_file(self, operation_data):
        """
        Save operation data to a file for offline storage
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            operation_id = operation_data.get(
                'operation_id', 'unknown').replace(
                '/', '_')
            operation_type = operation_data.get('operation_type', 'unknown')
            filename = f"uploads/pending/{operation_type}_{operation_id}_{timestamp}.json"

            # Ensure directory exists
            os.makedirs(os.path.dirname(filename), exist_ok=True)

            # Use custom encoder for datetime objects
            with open(filename, 'w') as f:
                json.dump(operation_data, f, indent=2, cls=DateTimeEncoder)

            self.logger.info(
                f"[UPLOAD] Saved {operation_type} operation {operation_id} to {filename}")

        except Exception as e:
            self.logger.error(
                f"[FILE SAVE ERROR] Failed to save operation to file: {str(e)}")

    def retry_failed_operations(self):
        """Retry all failed operations"""
        failed_count = self.failed_queue.qsize()
        if failed_count == 0:
            self.logger.info("[UPLOAD] No failed operations to retry")
            return 0

        self.logger.info(f"[UPLOAD] Retrying {failed_count} failed operations")
        retried = 0

        while not self.failed_queue.empty():
            try:
                operation_data = self.failed_queue.get_nowait()
                operation_id = operation_data.get('operation_id', 'unknown')
                operation_type = operation_data.get(
                    'operation_type', 'unknown')
                needs_azure_retry = operation_data.get('needs_azure_retry', True)
                needs_api_retry = operation_data.get('needs_api_retry', True)

                # Only retry what's needed
                if needs_azure_retry or needs_api_retry:
                    retry_desc = []
                    if needs_azure_retry:
                        retry_desc.append("Azure")
                    if needs_api_retry:
                        retry_desc.append("API")
                    
                    self.logger.info(
                        f"[UPLOAD RETRY] Retrying {operation_type} operation {operation_id} ({' and '.join(retry_desc)})")
                    
                    if needs_azure_retry and not needs_api_retry:
                        self._update_status(
                            operation_id,
                            UploadStatus.RETRYING,
                            f"Retrying Azure only (API already succeeded)")
                    elif not needs_azure_retry and needs_api_retry:
                        self._update_status(
                            operation_id,
                            UploadStatus.RETRYING,
                            f"Retrying API only (Azure already succeeded)")
                    else:
                        self._update_status(
                            operation_id,
                            UploadStatus.RETRYING,
                            f"Retrying both Azure and API")

                    # Add back to main queue
                    self.operation_queue.put(operation_data)
                    retried += 1
                else:
                    self.logger.info(
                        f"[UPLOAD RETRY] Skipping operation {operation_id} - both Azure and API already succeeded")

            except queue.Empty:
                break
            except Exception as e:
                self.logger.error(
                    f"[RETRY ERROR] Failed to retry operation: {str(e)}")

        self.logger.info(f"[UPLOAD] {retried} operations queued for retry")
        return retried

    def get_stats(self):
        """
        Get detailed upload statistics
        """
        stats = self.stats.copy()
        stats['queued_now'] = self.operation_queue.qsize()
        stats['failed_now'] = self.failed_queue.qsize()
        stats['in_progress'] = len(self.in_progress_operations)
        stats['azure_initialized'] = self.azure_initialized
        stats['uptime'] = str(datetime.now() - self.stats['start_time'])

        # Add performance stats
        stats.update(self.performance)

        # Add chunk upload stats
        stats['chunked_upload_percentage'] = (self.stats['chunked_uploads'] / max(
            1, self.stats['azure_uploads'])) * 100 if self.azure_initialized else 0

        # Add recent history summary
        if self.operation_history:
            recent_success = sum(
                1 for op in self.operation_history[-10:] if op['success'])
            recent_total = min(10, len(self.operation_history))
            stats['recent_success_rate'] = f"{recent_success}/{recent_total}"

            # Count partial successes
            recent_partial = sum(
                1 for op in self.operation_history[-10:] if op.get('azure_success', False) != op.get('api_success', False))
            stats['recent_partial_successes'] = recent_partial

            # Count by operation type in recent history
            recent_cleaning = sum(
                1 for op in self.operation_history[-10:] if op.get('operation_type') == 'manhole_cleaning')
            recent_inspection = sum(
                1 for op in self.operation_history[-10:] if op.get('operation_type') == 'pipe_inspection')
            stats['recent_cleaning'] = recent_cleaning
            stats['recent_inspection'] = recent_inspection

        return stats

    def get_detailed_status(self):
        """Get detailed status of all operations"""
        status = {
            'stats': self.get_stats(),
            'in_progress': list(
                self.in_progress_operations.values()),
            'queue_size': self.operation_queue.qsize(),
            'failed_size': self.failed_queue.qsize(),
            'azure_status': {
                'initialized': self.azure_initialized,
                'container': self.container_client.container_name if self.container_client else None,
                'chunk_size_mb': self.chunk_size / 1024 / 1024,
                'max_simple_upload_mb': self.max_simple_upload_size / 1024 / 1024,
                'video_chunk_threshold_mb': self.video_chunk_threshold / 1024 / 1024},
            'api_endpoint': self.api_url,
            'thread_running': self.is_running}
        return status

    def get_operation_status(self, operation_id):
        """Get status of a specific operation"""
        if operation_id in self.in_progress_operations:
            return self.in_progress_operations[operation_id]

        # Check history
        for op in reversed(self.operation_history):
            if op.get('operation_id') == operation_id:
                return {
                    'operation_id': operation_id,
                    'operation_type': op.get('operation_type', 'unknown'),
                    'status': 'completed',
                    'success': op.get('success', False),
                    'azure_success': op.get('azure_success', False),
                    'api_success': op.get('api_success', False),
                    'timestamp': op.get('timestamp'),
                    'duration': op.get('duration'),
                    'chunked_upload': op.get('chunked_upload', False)
                }

        return {'operation_id': operation_id, 'status': 'not_found'}

    def clear_queues(self):
        """Clear all queues"""
        cleared_ops = 0
        cleared_failed = 0

        while not self.operation_queue.empty():
            try:
                self.operation_queue.get_nowait()
                self.operation_queue.task_done()
                cleared_ops += 1
            except BaseException:
                pass

        while not self.failed_queue.empty():
            try:
                self.failed_queue.get_nowait()
                cleared_failed += 1
            except BaseException:
                pass

        self.logger.info(
            f"[UPLOAD] Cleared {cleared_ops} queued and {cleared_failed} failed operations")
        return cleared_ops + cleared_failed

    def test_api_connection(self):
        """Test API connection and response"""
        try:
            self.logger.info("[API TEST] Testing API connection...")

            # Test JSON data for manhole cleaning
            test_data = {
                "manhole_id": "TEST_" + datetime.now().strftime("%H%M%S"),
                "device_id": "TEST_DEVICE",
                "operation_id": "test_operation",
                "operation_type": "manhole_cleaning",
                "before_image_url": "https://example.com/before.jpg",
                "after_image_url": "https://example.com/after.jpg",
                "video_url": "N/A",
                "latitude": 17.4569,
                "longitude": 78.3711,
                "gps_fix": True,
                "updated_at": datetime.now().isoformat(),
                "district": "Hyderabad",
                "division": "Division 15",
                "area": "Kondapur",
                "duration_seconds": 5,
                "start_time": datetime.now().isoformat(),
                "end_time": datetime.now().isoformat(),
                "pipe_inspection_starttime": "N/A",
                "pipe_inspection_endtime": "N/A",
                "pipe_inspection_operationtime": 0
            }

            # Try JSON approach first
            json_str = json.dumps(test_data, cls=DateTimeEncoder)
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            
            response = requests.post(
                self.api_url, 
                data=json_str,
                headers=headers,
                timeout=10
            )
            
            self.logger.info(f"[API TEST] Status: {response.status_code}")
            self.logger.info(f"[API TEST] Response: {response.text[:200]}")

            return response.status_code == 200

        except Exception as e:
            self.logger.error(f"[API TEST ERROR] {str(e)}")
            return False

    def __del__(self):
        """Cleanup when Uploader is destroyed"""
        self.stop_upload_thread()


if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Simple test
    uploader = Uploader()
    print("✅ Uploader initialized with 100MB+ video support")
    print(f"📊 Chunk size: {uploader.chunk_size/1024/1024}MB")
    print(f"📊 Max simple upload: {uploader.max_simple_upload_size/1024/1024}MB")
    print(f"🎥 Video chunk threshold: {uploader.video_chunk_threshold/1024/1024}MB")
    print("\nFeatures:")
    print("  • Streaming chunked upload for large videos")
    print("  • Retry policies for Azure connections")
    print("  • Progress logging for long uploads")
    print("  • Memory-efficient uploads (no full file loading)")
    print("  • Partial success tracking (Azure vs API)")
    print("  • JSON data format for API calls")
    print("  • Smart retry logic (only retry failed components)")
    print("\n📋 DATA STRUCTURE SENT TO DATABASE:")
    print("For MANHOLE CLEANING:")
    print("- manhole_id, device_id, operation_id")
    print("- operation_type: 'manhole_cleaning'")
    print("- before_image_url, after_image_url (Azure URLs or local)")
    print("- video_url: 'N/A'")
    print("- latitude, longitude, gps_fix")
    print("- district, division, area (from config)")
    print("- duration_seconds, start_time, end_time")
    print("- pipe_inspection_* fields: 'N/A' or 0")
    print("\nFor PIPE INSPECTION:")
    print("- manhole_id, device_id, operation_id")
    print("- operation_type: 'pipe_inspection'")
    print("- video_url (Azure URL or local)")
    print("- before_image_url, after_image_url: 'N/A'")
    print("- pipe_inspection_starttime, pipe_inspection_endtime")
    print("- pipe_inspection_operationtime (duration in seconds)")