export function newer(candidate, previous) {
  if (!previous) return true;
  if (candidate.run_generation !== previous.run_generation) return candidate.run_generation > previous.run_generation;
  return candidate.run_id === previous.run_id && candidate.step >= previous.step;
}

export function createPoller({ request, render, failed, visible, schedule = setTimeout, cancel = clearTimeout }) {
  let timer, controller, revision = 0, latest = null;
  async function poll(ticket) {
    if (ticket !== revision || !visible()) return;
    controller = new AbortController();
    const timeout = schedule(() => controller.abort(), 5000);
    try {
      const data = await request(controller.signal);
      if (ticket !== revision || !visible()) return;
      if (newer(data, latest)) { latest = data; render(data); }
    } catch (error) {
      if (ticket === revision && visible()) failed(error);
    } finally {
      cancel(timeout);
      if (ticket === revision && visible()) timer = schedule(() => poll(ticket), 1000);
    }
  }
  return {
    resume() { revision++; cancel(timer); controller?.abort(); if (visible()) void poll(revision); },
    pause() { revision++; cancel(timer); controller?.abort(); },
  };
}
