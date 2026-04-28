import os
import asyncio
import uuid
import shutil
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from backend.config import events
from backend.file_services import process_binary_file

DROPZONE_DIR = Path(__file__).parent.parent / "Drop_To_Brain"
ARCHIVE_DIR = DROPZONE_DIR / "Archive_归档"

# Ensure directories exist
DROPZONE_DIR.mkdir(parents=True, exist_ok=True)
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

class DropZoneHandler(FileSystemEventHandler):
    def __init__(self, loop, queue):
        self.loop = loop
        self.queue = queue
        
    def on_created(self, event):
        if event.is_directory:
            return
            
        filepath = event.src_path
        filename = os.path.basename(filepath)
        
        # Ignore files starting with . or temp extensions
        if filename.startswith('.') or filename.endswith('.tmp') or filename.endswith('.crdownload'):
            return
            
        # Push to async queue safely from this thread back to the main asyncio loop
        self.loop.call_soon_threadsafe(self.queue.put_nowait, filepath)

    def on_moved(self, event):
        # Renaming into the folder triggers move sometimes
        if event.is_directory: return
        filepath = event.dest_path
        if os.path.dirname(filepath) != str(DROPZONE_DIR): return
        filename = os.path.basename(filepath)
        if filename.startswith('.') or filename.endswith('.tmp') or filename.endswith('.crdownload'): return
        self.loop.call_soon_threadsafe(self.queue.put_nowait, filepath)


async def consume_dropzone_queue(queue):
    while True:
        filepath = await queue.get()
        
        if not os.path.exists(filepath):
            queue.task_done()
            continue
            
        # Wait until the file is completely written (file handle released)
        max_attempts = 20
        file_ready = False
        for _ in range(max_attempts):
            try:
                # If we can read it fully it implies nobody is locking it exclusively 
                with open(filepath, 'rb') as f:
                    pass
                # ensure size isn't drastically changing
                size1 = os.path.getsize(filepath)
                await asyncio.sleep(0.5)
                size2 = os.path.getsize(filepath)
                if size1 == size2:
                    file_ready = True
                    break
            except Exception:
                pass
            await asyncio.sleep(1)
            
        if not file_ready:
            print(f"⚠️ [DropZone] 文件 {filepath} 持续被占用，防抱死触发，放弃当前队列处理。")
            queue.task_done()
            continue
            
        try:
            filename = os.path.basename(filepath)
            await events.publish(f"📥 [DropZone] 本地文件拖入: {filename}，正在处理...")
            
            with open(filepath, "rb") as f:
                content = f.read()
                
            item_id = uuid.uuid4().hex[:12]
            # Call our existing brain processing pipeline
            await process_binary_file(item_id, content, filename, space="default", force_title="")
            
            # Move to archive
            archive_path = ARCHIVE_DIR / f"{item_id}_{filename}"
            shutil.move(filepath, archive_path)
            
            await events.publish(f"✅ [DropZone] 处理完成，文件已归档。")
        except Exception as e:
            print(f"DropZone Error processing {filepath}: {e}")
            await events.publish(f"❌ [DropZone] 处理中断 {filepath}: {str(e)}")
        finally:
            queue.task_done()


class DropZoneWorker:
    def __init__(self):
        self.observer = None
        self.consumer_task = None
        
    async def start(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        
        # Start consumer task
        self.consumer_task = asyncio.create_task(consume_dropzone_queue(queue))
        
        # Start watchdog observer in a thread
        event_handler = DropZoneHandler(loop, queue)
        self.observer = Observer()
        self.observer.schedule(event_handler, str(DROPZONE_DIR), recursive=False)
        self.observer.start()
        print(f"\n{'='*50}\n[DropZone] Local directory watch started\nDir: {DROPZONE_DIR}\n{'='*50}\n")

    async def stop(self):
        print(f"停止 DropZone 目录监听...")
        if self.observer:
            self.observer.stop()
            self.observer.join()
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass
        print(f"DropZone 监听已安全停止。")

# Global instance
dropzone_worker = DropZoneWorker()

async def start_dropzone_worker():
    await dropzone_worker.start()
    
async def stop_dropzone_worker():
    await dropzone_worker.stop()
