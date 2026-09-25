import { Component, input } from '@angular/core';
const paths: Record<string, string> = {
  overview: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  markets: 'M3 17l6-6 4 4 8-10 M15 5h6v6',
  strategies: 'M12 3v5 M5 16v5 M19 16v5 M5 16h14V8H5z',
  backtests: 'M9 3h6 M10 3v6l-6 10q-1 2 2 2h12q3 0 2-2L14 9V3 M8 15h8',
  results: 'M4 20V10 M10 20V4 M16 20v-7 M22 20H2',
  charts: 'M3 3h18v18H3z M12 3v18 M3 12h18',
  settings:
    'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  profile: 'M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M4 21v-3a8 6 0 0 1 16 0v3',
  logout: 'M9 4H3v16h6 M9 12h12 M17 8l4 4-4 4',
  arrow: 'M5 12h14 M14 7l5 5-5 5',
  search: 'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
  plus: 'M12 4v16 M4 12h16',
  refresh: 'M20 7a9 9 0 1 0 1 8 M20 2v6h-6',
  menu: 'M3 6h18 M3 12h18 M3 18h18',
  close: 'M5 5l14 14 M5 19L19 5',
  check: 'M4 12l5 5L20 6',
  shield: 'M12 2l8 4v6q0 6-8 10-8-4-8-10V6z M8 12l3 3 5-6',
  wallet: 'M3 6h18v15H3z M3 6l14-4v4 M16 12h5v4h-5z',
  download: 'M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4',
  bolt: 'M13 2L4 14h7l-1 8 10-13h-7z',
  star: 'M12 3l3 6 7 1-5 5 1 7-6-3-6 3 1-7-5-5 7-1z',
};
@Component({
  selector: 'ax-icon',
  template:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path [attr.d]="path()"/></svg>',
  styles:
    ':host{display:inline-flex;width:19px;height:19px;flex-shrink:0}svg{width:100%;height:100%}',
})
export class Icon {
  name = input('overview');
  path() {
    return paths[this.name()] || paths['overview'];
  }
}
