'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import SettingsModal, { useSettings } from '@/components/SettingsModal';
import { ChatHeader } from '@/components/chat/ChatHeader';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessages } from '@/components/chat/ChatMessages';
import { ErrorBanner } from '@/components/chat/ErrorBanner';
import { useAutoScroll } from '@/components/chat/useAutoScroll';
import type { ChatBubble } from '@/components/chat/types';

// New messages and the assistant's working state are pushed over Server-Sent Events (P18).
// The full history is only fetched again if the stream is down, as a slow fallback.
const FALLBACK_POLL_MS = 15000;
const WAITING_SAFETY_MS = 120000; // never leave the typing indicator on forever if the stream is lost

const formatEscapeCharacters = (text: string): string => {
  return text
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\r/g, '\r')
    .replace(/\\\\/g, '\\');
};

const isRenderableMessage = (entry: any) =>
  typeof entry?.role === 'string' &&
  typeof entry?.content === 'string' &&
  entry.content.trim().length > 0;

// A message's sequence number (its place in the conversation log) is its stable id
const toBubble = (message: any, fallbackIndex: number): ChatBubble => ({
  id: typeof message.seq === 'number' ? `seq-${message.seq}` : `history-${fallbackIndex}`,
  role: message.role,
  text: formatEscapeCharacters(message.content),
});

const toBubbles = (payload: any): ChatBubble[] => {
  if (!Array.isArray(payload?.messages)) return [];

  return payload.messages.filter(isRenderableMessage).map(toBubble);
};

const lastSeqOf = (payload: any): number =>
  Array.isArray(payload?.messages)
    ? payload.messages.reduce((max: number, m: any) => (typeof m?.seq === 'number' && m.seq > max ? m.seq : max), 0)
    : 0;

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isWaitingForResponse, setIsWaitingForResponse] = useState(false);
  const { scrollContainerRef, handleScroll } = useAutoScroll({
    items: messages,
    isWaiting: isWaitingForResponse,
  });
  const openSettings = useCallback(() => setOpen(true), [setOpen]);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const lastSeqRef = useRef(0);
  const sourceRef = useRef<EventSource | null>(null);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      setMessages(toBubbles(data));
      lastSeqRef.current = lastSeqOf(data);
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      console.error('Failed to load chat history', err);
    }
  }, []);

  // The live stream: it first replays the history, then pushes each new message as it is written.
  // On reconnect the browser sends the last event id, so the server resumes without gaps.
  useEffect(() => {
    const source = new EventSource('/api/chat/events');
    sourceRef.current = source;

    source.addEventListener('message', (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      if (!isRenderableMessage(data) || data.seq <= lastSeqRef.current) return;
      lastSeqRef.current = data.seq;
      const bubble = toBubble(data, data.seq);
      setMessages(prev => {
        // The server's copy of a message we showed optimistically replaces the optimistic one
        const pending = bubble.role === 'user'
          ? prev.findIndex(m => m.id.startsWith('pending-') && m.text === bubble.text)
          : -1;
        if (pending >= 0) return [...prev.slice(0, pending), ...prev.slice(pending + 1), bubble];
        return [...prev, bubble];
      });
    });

    source.addEventListener('status', (event) => {
      const data = JSON.parse((event as MessageEvent).data);
      setIsWaitingForResponse(Boolean(data.working));
    });

    source.addEventListener('cleared', () => {
      lastSeqRef.current = 0;
      setMessages([]);
    });

    return () => {
      source.close();
      sourceRef.current = null;
    };
  }, []);

  // Fallback only: if the stream is down, refresh the history occasionally
  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (sourceRef.current?.readyState !== EventSource.OPEN) void loadHistory();
    }, FALLBACK_POLL_MS);
    return () => window.clearInterval(intervalId);
  }, [loadHistory]);

  useEffect(() => {
    if (!isWaitingForResponse) return;
    const timeoutId = window.setTimeout(() => setIsWaitingForResponse(false), WAITING_SAFETY_MS);
    return () => window.clearTimeout(timeoutId);
  }, [isWaitingForResponse]);

  // Detect and store browser timezone on first load
  useEffect(() => {
    const detectAndStoreTimezone = async () => {
      // Only run if timezone not already stored
      if (settings.timezone) return;
      
      try {
        const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        
        // Send to server
        const response = await fetch('/api/timezone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timezone: browserTimezone }),
        });
        
        if (response.ok) {
          // Update local settings
          setSettings({ ...settings, timezone: browserTimezone });
        }
      } catch (error) {
        // Fail silently - timezone detection is not critical
        console.debug('Timezone detection failed:', error);
      }
    };

    void detectAndStoreTimezone();
  }, [settings, setSettings]);

  const canSubmit = input.trim().length > 0;
  const inputPlaceholder = 'Type a message…';

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;

      setError(null);
      setIsWaitingForResponse(true);

      // Optimistically add the user message immediately; the stream's copy replaces it when it arrives
      const userMessage: ChatBubble = {
        id: `pending-${Date.now()}`,
        role: 'user',
        text: formatEscapeCharacters(trimmed),
      };
      setMessages(prev => {
        const newMessages = [...prev, userMessage];
        return newMessages;
      });

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: [{ role: 'user', content: trimmed }],
          }),
        });

        if (!(res.ok || res.status === 202)) {
          const detail = await res.text();
          throw new Error(detail || `Request failed (${res.status})`);
        }
      } catch (err: any) {
        console.error('Failed to send message', err);
        setError(err?.message || 'Failed to send message');
        // Remove the optimistic message on error
        setMessages(prev => prev.filter(msg => msg.id !== userMessage.id));
        setIsWaitingForResponse(false);
        throw err instanceof Error ? err : new Error('Failed to send message');
      }
      // No polling: the reply, and the end of the assistant's work, arrive over the event stream
    },
    [],
  );

  const handleClearHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { method: 'DELETE' });
      if (!res.ok) {
        console.error('Failed to clear chat history', res.statusText);
        return;
      }
      lastSeqRef.current = 0;
      setMessages([]);
    } catch (err) {
      console.error('Failed to clear chat history', err);
    }
  }, [setMessages]);

  const triggerClearHistory = useCallback(() => {
    void handleClearHistory();
  }, [handleClearHistory]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    const value = input;
    setInput('');
    try {
      await sendMessage(value);
    } catch {
      setInput(value);
    }
  }, [canSubmit, input, sendMessage, setInput]);

  const handleInputChange = useCallback((value: string) => {
    setInput(value);
  }, [setInput]);

  const clearError = useCallback(() => setError(null), [setError]);

  return (
    <main className="chat-bg min-h-screen p-4 sm:p-6">
      <div className="chat-wrap flex flex-col">
        <ChatHeader onOpenSettings={openSettings} onClearHistory={triggerClearHistory} />

        <div className="card flex-1 overflow-hidden">
          <ChatMessages
            messages={messages}
            isWaitingForResponse={isWaitingForResponse}
            scrollContainerRef={scrollContainerRef}
            onScroll={handleScroll}
          />

          <div className="border-t border-gray-200 p-3">
            {error && <ErrorBanner message={error} onDismiss={clearError} />}

            <ChatInput
              value={input}
              canSubmit={canSubmit}
              placeholder={inputPlaceholder}
              onChange={handleInputChange}
              onSubmit={handleSubmit}
            />
          </div>
        </div>

        <SettingsModal open={open} onClose={closeSettings} settings={settings} onSave={setSettings} />
      </div>
    </main>
  );
}
