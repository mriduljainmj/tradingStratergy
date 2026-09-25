import { Injectable, signal } from '@angular/core';
import { ExecutionEvent } from './models';
@Injectable({ providedIn: 'root' })
export class Feedback {
  executions = signal<ExecutionEvent[]>([]);
  acknowledgeExecution() {
    this.executions.update((events) => events.slice(1));
  }
  toast = signal('');
  private timer: any;
  confirmation = signal<{ title: string; body: string; label: string; danger: boolean } | null>(
    null,
  );
  private resolve?: (confirmed: boolean) => void;
  notify(text: string) {
    this.toast.set(text);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.toast.set(''), 4500);
  }
  confirm(title: string, body: string, label = 'Confirm', danger = false) {
    return new Promise<boolean>((resolve) => {
      this.resolve?.(false);
      this.resolve = resolve;
      this.confirmation.set({ title, body, label, danger });
    });
  }
  answer(yes: boolean) {
    this.confirmation.set(null);
    this.resolve?.(yes);
    this.resolve = undefined;
  }
}
