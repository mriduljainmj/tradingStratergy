import { Theme } from './core/theme';
import { Component, inject, effect } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { CurrencyPipe, DatePipe } from '@angular/common';
import { Feedback } from './core/feedback';
import { Icon } from './shared/icon';
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, Icon, CurrencyPipe, DatePipe],
  template: `<a class="skip-link" href="#main-content">Skip to main content</a><router-outlet />
    @if (feedback.toast()) {
      <div class="toast" role="status">
        <span class="toast-check"><ax-icon name="check" /></span>{{ feedback.toast()
        }}<button
          class="icon-btn"
          aria-label="Dismiss notification"
          (click)="feedback.toast.set('')"
        >
          <ax-icon name="close" />
        </button>
      </div>
    }
    @if (feedback.confirmation(); as c) {
      <div class="modal-backdrop confirmation-layer">
        <section
          class="modal confirm-modal"
          data-confirm
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="confirmation-title"
          aria-describedby="confirmation-body"
        >
          <div class="confirm-symbol"><ax-icon [name]="c.danger ? 'shield' : 'bolt'" /></div>
          <h2 id="confirmation-title">{{ c.title }}</h2>
          <p id="confirmation-body">{{ c.body }}</p>
          <div class="modal-actions">
            <button class="btn" (click)="feedback.answer(false)">Cancel</button
            ><button
              class="btn"
              [class.danger]="c.danger"
              [class.primary]="!c.danger"
              (click)="feedback.answer(true)"
            >
              {{ c.label }}
            </button>
          </div>
        </section>
      </div>
    } @else if (feedback.executions()[0]; as trade) {
      <div class="modal-backdrop confirmation-layer">
        <section
          class="modal confirm-modal"
          data-confirm
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="execution-title"
          aria-describedby="execution-body"
        >
          <div class="confirm-symbol"><ax-icon name="check" /></div>
          <h2 id="execution-title">
            {{ trade.mode === 'LIVE' ? 'Live trade executed' : 'Paper trade executed' }}
          </h2>
          <div id="execution-body">
            <p>{{ trade.action === 'BUY' ? 'Entry · Buy' : 'Exit · Sell' }} · {{ trade.symbol }}</p>
            <p>{{ trade.quantity }} units at {{ trade.price | currency: 'INR' }} per unit</p>
            <p>
              {{ trade.time | date: 'dd MMM yyyy, h:mm:ss a' : '+0530' }} IST · {{ trade.reason }}
            </p>
            <p>
              {{
                trade.mode === 'PAPER'
                  ? 'This is a simulated fill; no broker order was placed.'
                  : 'The broker has confirmed this fill.'
              }}
            </p>
            <p class="muted">This trade has already executed. Acknowledging does not change it.</p>
          </div>
          <div class="modal-actions">
            <button class="btn primary" (click)="feedback.acknowledgeExecution()">Got it</button>
          </div>
        </section>
      </div>
    }`,
  host: { '(document:keydown)': 'keys($event)' },
})
export class App {
  theme = inject(Theme);
  feedback = inject(Feedback);
  private previous: HTMLElement | null = null;
  constructor() {
    effect(() => {
      if (this.feedback.confirmation() || this.feedback.executions().length) {
        if (!document.activeElement?.closest('[data-confirm]')) {
          this.previous = document.activeElement as HTMLElement;
        }
        setTimeout(() =>
          document.querySelector<HTMLButtonElement>('[data-confirm] button')?.focus(),
        );
      } else {
        this.previous?.focus();
      }
    });
  }
  keys(e: KeyboardEvent) {
    if (!this.feedback.confirmation() && !this.feedback.executions().length) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      if (this.feedback.confirmation()) this.feedback.answer(false);
      else this.feedback.acknowledgeExecution();
    }
    if (e.key === 'Tab') {
      const buttons = document.querySelectorAll<HTMLButtonElement>('[data-confirm] button');
      const first = buttons[0],
        last = buttons[buttons.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }
}
