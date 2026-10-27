// Live conversation events (P18): proxies the Python server's Server-Sent Events stream to the browser.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
const eventsPath = `${serverBase.replace(/\/$/, '')}/api/v1/chat/events`;

export async function GET(req: Request) {
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  // A reconnecting EventSource sends the id of the last event it received, so the server can resume from there
  const lastEventId = req.headers.get('last-event-id');
  if (lastEventId) headers['Last-Event-ID'] = lastEventId;

  try {
    const upstream = await fetch(eventsPath, { headers, cache: 'no-store', signal: req.signal });
    if (!upstream.ok || !upstream.body) {
      return new Response(`event stream unavailable (${upstream.status})`, { status: 502 });
    }
    return new Response(upstream.body, {
      headers: {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        Connection: 'keep-alive',
      },
    });
  } catch (error: any) {
    return new Response(error?.message || 'Failed to reach Python server', { status: 502 });
  }
}
