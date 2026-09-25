import { Component, inject } from '@angular/core';
import { Theme } from '../core/theme';
@Component({
  selector: 'ax-theme-picker',
  template: `<select
    aria-label="Color theme"
    [value]="theme.choice()"
    (change)="theme.set($any($event.target).value)"
  >
    <option value="light">Light theme</option>
    <option value="dark">Dark theme</option>
    <option value="system">System theme</option>
  </select>`,
  styles: `
    :host {
      display: inline-block;
    }
    select {
      width: 118px;
      font-size: 11px;
      padding: 7px;
      min-height: 34px;
    }
  `,
})
export class ThemePicker {
  theme = inject(Theme);
}
