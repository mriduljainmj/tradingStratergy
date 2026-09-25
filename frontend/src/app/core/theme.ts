import { Injectable, signal } from '@angular/core';

export type ThemeChoice = 'light' | 'dark' | 'system';

@Injectable({ providedIn: 'root' })
export class Theme {
  choice = signal<ThemeChoice>('system');
  private system = window.matchMedia('(prefers-color-scheme: dark)');
  constructor() {
    try {
      const saved = localStorage.getItem('axiom_theme');
      if (saved === 'light' || saved === 'dark' || saved === 'system') this.choice.set(saved);
    } catch {
      /* Theme remains usable when browser storage is restricted. */
    }
    this.apply();
    this.system.addEventListener('change', () => this.apply());
    window.addEventListener('storage', (event) => {
      if (event.key === 'axiom_theme') {
        this.choice.set(
          event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : 'system',
        );
        this.apply();
      }
    });
  }
  set(value: string) {
    if (value !== 'light' && value !== 'dark' && value !== 'system') return;
    this.choice.set(value);
    try {
      localStorage.setItem('axiom_theme', value);
    } catch {
      /* In-memory preference still works. */
    }
    this.apply();
  }
  private apply() {
    const dark = this.choice() === 'dark' || (this.choice() === 'system' && this.system.matches);
    document.documentElement.dataset['theme'] = dark ? 'dark' : 'light';
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', dark ? '#0e1622' : '#117d67');
  }
}
