"""Translate off the NVDA thread; deliver on its event queue, with cancellation."""
from collections import OrderedDict, deque
import threading
import time


class SpeechDispatcher:
    def __init__(self, translate, schedule, deliver, metrics=None, persistent_cache=None,
                 resolve_source=None, language_command=None, cache_application=None):
        self.translate, self.schedule, self.deliver = translate, schedule, deliver
        self.metrics = metrics or (lambda **kw: None)
        self.condition = threading.Condition()
        self.pending = deque()
        self.cache = OrderedDict()
        self.persistent_cache = persistent_cache
        self.resolve_source = resolve_source
        self.language_command = language_command
        self.cache_application=cache_application or (lambda app:app)
        self.generation = 0
        self.closed = False
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        with self.condition:
            self.generation += 1
            self.pending.clear()

    def close(self):
        with self.condition:
            self.closed = True
            self.generation += 1
            self.pending.clear()
            self.condition.notify_all()

    def invalidate_cache(self):
        with self.condition:
            self.generation+=1
            self.pending.clear()
            self.cache.clear()

    def submit(self, sequence, args, kwargs, app, src, tgt, document_languages=None):
        with self.condition:
            if self.closed:
                return
            # Bound backlog during continuous unsolicited announcements.
            if len(self.pending) >= 64:
                self.pending.popleft()
            self.pending.append((self.generation, sequence, args, kwargs, app, src, tgt, document_languages, time.perf_counter()))
            self.condition.notify()

    def _deliver(self, job, output, hit_count):
        with self.condition:
            if self.closed or job[0] != self.generation:
                return
        self.metrics(total_ms=(time.perf_counter()-job[-1])*1000, cache_hits=hit_count)
        self.deliver(output, job[2], job[3], job[4], job[5], job[6])

    def _run(self):
        while True:
            with self.condition:
                while not self.pending and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                job = self.pending.popleft()
            generation, sequence, args, kwargs, app, src, tgt, document_languages, started = job
            output, hits = [], 0
            text_index, current_language = 0, None
            for item in sequence:
                with self.condition:
                    if self.closed or generation != self.generation:
                        break
                if self.language_command is not None and isinstance(item, self.language_command):
                    current_language = item.lang
                document = None
                if isinstance(item, str):
                    if document_languages is not None and text_index < len(document_languages):
                        document = document_languages[text_index]
                    text_index += 1
                if not isinstance(item, str) or not item.strip() or len(item.strip()) <= 1 or not any(c.isalpha() for c in item):
                    output.append(item)
                    continue
                try:
                    effective_src = self.resolve_source(app, src, document, item) if self.resolve_source else src
                except Exception:
                    output.append(item)
                    continue
                key = (self.cache_application(app), effective_src, tgt, item)
                with self.condition:value = self.cache.pop(key, None)
                if value is None and self.persistent_cache is not None:
                    value = self.persistent_cache.get(key)
                if value is not None:
                    hits += 1
                else:
                    try:
                        value = self.translate(item, effective_src, tgt, app=app)
                        if not isinstance(value, str) or not value.strip():
                            raise ValueError('empty translation')
                    except Exception:
                        output.append(item)  # do not persist transient failures
                        continue
                    if self.persistent_cache is not None:
                        self.persistent_cache.put(key, value)
                with self.condition:
                    if self.closed or generation!=self.generation:break
                    self.cache[key] = value
                    while len(self.cache) > 1024:self.cache.popitem(last=False)
                if self.language_command is not None and value != item:
                    output.extend((self.language_command(tgt), value, self.language_command(current_language)))
                else:
                    output.append(value)
            else:
                self.schedule(self._deliver, job, output, hits)
