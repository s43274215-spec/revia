export class SinglePromiseGate<T> {
  private current: Promise<T> | null = null;

  run(factory: () => Promise<T>): Promise<T> {
    if (this.current) return this.current;
    const request = factory();
    const guarded = request.finally(() => {
      if (this.current === guarded) this.current = null;
    });
    this.current = guarded;
    return guarded;
  }

  get pending(): boolean {
    return this.current !== null;
  }
}

export function isTransientNetworkError(error: unknown): boolean {
  if (error instanceof TypeError) return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /failed to fetch|networkerror|network request failed|load failed/i.test(message);
}
