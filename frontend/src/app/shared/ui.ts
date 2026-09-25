import { Component, input } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { Icon } from './icon';
@Component({
  selector: 'ax-heading',
  template: `<div class="page-heading">
    <div>
      <div class="eyebrow">{{ eyebrow() }}</div>
      <h1>{{ title() }}</h1>
      <p>{{ subtitle() }}</p>
    </div>
    <div class="heading-actions"><ng-content /></div>
  </div>`,
})
export class Heading {
  title = input('');
  subtitle = input('');
  eyebrow = input('YOUR TRADING WORKSPACE');
}
@Component({
  selector: 'ax-stat',
  imports: [DecimalPipe, Icon],
  template: `<article class="stat-card">
    <div class="stat-label">{{ label() }}<ax-icon [name]="icon()" /></div>
    <strong [class.positive]="tone() === 'positive'" [class.negative]="tone() === 'negative'"
      >{{ prefix() }}{{ value() === null ? '—' : (value() | number: '1.0-2')
      }}{{ suffix() }}</strong
    ><span class="stat-note">{{ note() }}</span>
  </article>`,
})
export class Stat {
  label = input('');
  value = input<number | null>(0);
  prefix = input('');
  suffix = input('');
  note = input('');
  icon = input('results');
  tone = input('');
}
@Component({
  selector: 'ax-error',
  template: `@if (text()) {
    <div class="alert error" role="alert">{{ text() }}</div>
  }`,
})
export class ErrorBox {
  text = input('');
}
