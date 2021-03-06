# -*- coding: utf-8 -*-
import logging
import os
import oss2
from oss2 import SizedFileAdapter, determine_part_size
from oss2.models import PartInfo
from task_queue import TaskQueue
from oss_client import get_oss_client
import json
import time


# Copy an object specified by key from src_bucket to dest_bucket using multipart download and upload.

# event format
# {
#   "src_bucket": "",
#   "dest_bucket": "",
#   "key": "",
#   "part_size": number,
#   "total_size": number
# }

clients = {}

def handler(event, context):
  logger = logging.getLogger()
  evt = json.loads(event)
  logger.info("Handling event: %s", evt)
  src_endpoint = 'https://oss-%s-internal.aliyuncs.com' % context.region
  src_bucket = evt["src_bucket"]
  src_client = clients.get(src_bucket)
  if src_client is None:
    src_client = get_oss_client(context, src_endpoint, src_bucket)
    clients[src_bucket] = src_client
  dest_bucket = evt["dest_bucket"]
  dest_client = clients.get(dest_bucket)
  if dest_client is None:
    dest_client = get_oss_client(context, evt.get("dest_oss_endpoint") or os.environ.get('DEST_OSS_ENDPOINT') or src_endpoint, dest_bucket, evt.get("dest_access_role"))
    clients[dest_bucket] = dest_client

  copy(src_client, dest_client, evt["key"], evt["part_size"], evt["total_size"])

  return {}


def copy(src_client, dest_client, key, part_size, total_size):
  logger = logging.getLogger()
  logger.info("Starting to copy %s", key)
  start_time = time.time()
  upload_id = dest_client.init_multipart_upload(key).upload_id

  def producer(queue):
    # Decide the number of parts
    num_of_parts = (total_size + part_size - 1) // part_size
    for part_no in range(1, num_of_parts+1):
      part_range = ((part_no-1)*part_size, min(part_no*part_size, total_size)-1)
      queue.put((part_no, part_range))


  parts = []
  def consumer(queue):
    while queue.ok():
      item = queue.get()
      if item is None:
          break

      part_no, part_range = item
      object_stream = src_client.get_object(key, byte_range=part_range)
      res = dest_client.upload_part(key, upload_id, part_no, object_stream)
      parts.append(PartInfo(part_no, res.etag))

  task_q = TaskQueue(producer, [consumer] * 16)
  task_q.run()

  dest_client.complete_multipart_upload(key, upload_id, parts)
  end_time = time.time()
  logger.info('Copied %s in %s secs', key, end_time-start_time)
