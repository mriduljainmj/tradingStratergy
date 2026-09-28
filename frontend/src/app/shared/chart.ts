import { drawingSymbol, drawingInterval, drawingPosition } from './drawing-time';
import {
  AfterViewInit,
  Component,
  ElementRef,
  OnChanges,
  OnDestroy,
  ViewChild,
  input,
  signal,
  inject,
} from '@angular/core';
import { Api, message, query } from '../core/api';
import { FormsModule } from '@angular/forms';
import { STUDIES, Study, StudyKind, calculateStudy } from './chart-indicators';
import { formatChartTime, formatChartTick, chartDate } from './chart-time';
import {
  createChart,
  IChartApi,
  ISeriesApi,
  ColorType,
  MismatchDirection,
} from 'lightweight-charts';
@Component({
  selector: 'ax-chart',
  imports: [FormsModule],
  host: {
    '[class.chart-selected]': 'selected()',
    '[attr.aria-busy]': 'loading()',
    '(document:pointerdown)': 'outside($event)',
    '(document:focusin)': 'outside($event)',
    '(document:keydown.escape)': 'cancelTool()',
    '(document:keydown)': 'drawingKey($event)',
  },
  template: `@if (type() === 'candle') {
      <div class="study-toolbar" (dblclick)="$event.stopPropagation()">
        <button type="button" (click)="panel.set(!panel())" [attr.aria-expanded]="panel()">
          Indicators <span>{{ studies().length || '' }}</span>
        </button>
        <input type="color" aria-label="Drawing color" [(ngModel)]="drawColor" />
        <button
          type="button"
          aria-label="Undo drawing"
          [disabled]="!drawings().length || locked()"
          (click)="undoDrawing()"
        >
          Undo
        </button>
        <button
          type="button"
          aria-label="Redo drawing"
          [disabled]="!redo.length || locked()"
          (click)="redoDrawing()"
        >
          Redo
        </button>
        <button type="button" (click)="objects.set(!objects())" [attr.aria-expanded]="objects()">
          Drawings {{ drawings().length || '' }}
        </button>
        @if (context()) {
          <button
            type="button"
            (click)="saveLayout()"
            [disabled]="layoutBusy() || !layoutReady || loading()"
          >
            Save drawings &amp; indicators
          </button>
          <span role="status">{{ layoutStatus() }}</span>
          @if (!layoutReady && !layoutBusy()) {
            <button type="button" (click)="restoreLayout()">Retry saved settings</button>
          }
        }
      </div>
      @if (tool()) {
        <div class="tool-hint" role="status">
          {{ tool() === 'brush' ? 'Drag to draw' : anchor ? 'End point' : 'Start point' }} ·
          {{ tool() }} · Esc cancels
          @if (tool() === 'text') {
            <input
              aria-label="Drawing note"
              placeholder="Your note"
              maxlength="80"
              [(ngModel)]="note"
            />
          }
          <button type="button" (click)="cancelTool()">Cancel</button>
        </div>
      }
      @if (panel()) {
        <div class="study-panel" aria-label="Indicator settings">
          <div class="study-toolbar">
            <button type="button" (click)="addRibbon()" [disabled]="loading() || !data().length">
              EMA ribbon · 10/21/50/200
            </button>
            <select
              aria-label="Technical indicator"
              [(ngModel)]="studyKind"
              (ngModelChange)="studyPeriod = studyDefinition().period"
            >
              @for (item of catalogue; track item.kind) {
                <option [value]="item.kind">{{ item.kind }} — {{ item.name }}</option>
              }
            </select>
            <label
              >Period
              <input
                type="number"
                aria-label="Indicator period"
                min="2"
                max="500"
                [(ngModel)]="studyPeriod"
                [disabled]="fixedPeriod()"
            /></label>
            <input type="color" aria-label="Indicator color" [(ngModel)]="studyColor" />
            <button type="button" (click)="addStudy()" [disabled]="loading() || !data().length">
              Add
            </button>
            <button type="button" aria-label="Close indicator settings" (click)="panel.set(false)">
              Close
            </button>
          </div>
          <p>
            Overlays share the price chart. One lower indicator at a time; adding another replaces
            it. Values use loaded candles; warm-up and unavailable volume remain gaps. Volume
            studies wait for historical volume refresh when a live candle changes. Use “Save
            drawings &amp; indicators” to keep your setup for this symbol across timeframes in your
            account.
          </p>
          @if (studyMessage()) {
            <p role="status">{{ studyMessage() }}</p>
          }
        </div>
      }
      @if (objects()) {
        <div class="drawing-list">
          <span>Select a drawing to edit it. Use Save to keep your changes.</span>
          @for (drawing of drawings(); track drawing.id) {
            <button
              type="button"
              (click)="selectDrawing(drawing.id)"
              [disabled]="locked()"
              [attr.aria-label]="'Select ' + drawing.kind + ' drawing'"
            >
              {{ drawing.kind }} {{ drawing.text || '' }}
            </button>
          }
          @if (!drawings().length) {
            <span>No drawings yet. Choose a tool from the left toolbar.</span>
          }
        </div>
      }
    }
    <div class="chart-body">
      @if (selectedDrawing(); as drawing) {
        <div class="drawing-editor" role="toolbar" aria-label="Selected drawing settings">
          <strong>{{ drawing.kind }} selected</strong>
          <label
            >Color
            <input
              type="color"
              aria-label="Selected drawing color"
              [disabled]="locked()"
              [ngModel]="drawing.color"
              (ngModelChange)="editSelected({ color: $event })"
          /></label>
          @if (drawing.kind === 'text') {
            <label
              >Text
              <input
                aria-label="Selected drawing text"
                maxlength="80"
                [disabled]="locked()"
                [ngModel]="drawing.text"
                (ngModelChange)="editSelected({ text: $event })"
            /></label>
          }
          <span>{{
            locked() ? 'Unlock drawings to edit.' : 'Drag endpoints to edit · Delete to remove'
          }}</span>
          <button type="button" (click)="deselectDrawing()">Done</button>
        </div>
      }

      @if (type() === 'candle') {
        <nav class="drawing-rail" aria-label="Drawing tools" (dblclick)="$event.stopPropagation()">
          <button
            type="button"
            title="Cursor · pan and zoom"
            aria-label="Cursor"
            [class.active]="!tool()"
            (click)="chooseTool('')"
          >
            <svg viewBox="0 0 24 24"><path d="M12 2v7m0 6v7M2 12h7m6 0h7" /></svg>
          </button>
          @for (group of drawingGroups; track group.label) {
            <button
              type="button"
              [title]="group.label"
              [attr.aria-label]="group.label"
              [attr.aria-expanded]="group.tools.length > 1 ? toolGroup() === group.label : null"
              [class.active]="groupActive(group)"
              [disabled]="loading() || !data().length || locked()"
              (click)="
                group.tools.length === 1
                  ? chooseTool(group.tools[0].kind)
                  : toggleToolGroup(group.label)
              "
            >
              <svg viewBox="0 0 24 24"><path [attr.d]="group.icon" /></svg>
              @if (group.tools.length > 1) {
                <span class="group-chevron">›</span>
              }
            </button>
          }
          <span class="rail-divider"></span>
          <button
            type="button"
            title="Snap to nearest candle OHLC"
            aria-label="Snap drawings to candle prices"
            [attr.aria-pressed]="snap()"
            [class.active]="snap()"
            (click)="snap.set(!snap())"
          >
            <svg viewBox="0 0 24 24">
              <path
                d="M5 15V9a7 7 0 0 1 14 0v6h-5V9a2 2 0 0 0-4 0v6zM5 11h5m4 0h5M5 18l2 3 2-3m6 0 2 3 2-3"
              />
            </svg>
          </button>
          <button
            type="button"
            title="Lock drawings"
            aria-label="Lock drawings"
            [attr.aria-pressed]="locked()"
            [class.active]="locked()"
            (click)="toggleLock()"
          >
            <svg viewBox="0 0 24 24">
              <path d="M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4M12 15v3" />
            </svg>
          </button>
          <button
            type="button"
            title="Hide drawings"
            aria-label="Hide drawings"
            [attr.aria-pressed]="hiddenDrawings()"
            [class.active]="hiddenDrawings()"
            (click)="hiddenDrawings.set(!hiddenDrawings())"
          >
            <svg viewBox="0 0 24 24">
              <path
                d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0"
              />
            </svg>
          </button>
          <span class="rail-divider"></span>
          <button
            type="button"
            [title]="
              selectedDrawing()
                ? 'Delete selected drawing · Delete key'
                : 'Remove all drawings · Redo restores them one at a time'
            "
            [attr.aria-label]="
              selectedDrawing() ? 'Delete selected drawing' : 'Remove all drawings'
            "
            [disabled]="locked() || !drawings().length"
            (click)="selectedDrawing() ? deleteSelected() : clearDrawings()"
          >
            <svg viewBox="0 0 24 24">
              <path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7" />
            </svg>
          </button>
        </nav>
        @if (toolGroup()) {
          <div class="drawing-flyout" role="menu" [attr.aria-label]="toolGroup()">
            @for (group of drawingGroups; track group.label) {
              @if (group.label === toolGroup()) {
                <strong>{{ group.label }}</strong>
                @for (item of group.tools; track item.kind) {
                  <button type="button" role="menuitem" (click)="chooseTool(item.kind)">
                    {{ item.label }}
                  </button>
                }
              }
            }
          </div>
        }
      }
      <div class="chart-main">
        @if (studies().length) {
          <div class="study-legend">
            @for (study of studies(); track study.id) {
              <div>
                <span>{{ study.kind }} {{ study.period }}</span
                ><strong [style.color]="study.color">{{
                  studyValues()[study.id] || 'warming up'
                }}</strong>
                <button
                  type="button"
                  (click)="removeStudy(study.id)"
                  [attr.aria-label]="'Remove ' + study.kind + ' indicator'"
                  title="Remove indicator"
                >
                  ×
                </button>
              </div>
            }
          </div>
        }
        <div
          class="chart-readout"
          aria-live="off"
          [style.visibility]="loading() ? 'hidden' : 'visible'"
        >
          {{ readout() || 'Market time · IST (UTC+05:30)' }}
        </div>
        <div class="chart-plot" (dblclick)="cancelTool()">
          <div
            class="chart-host"
            #host
            tabindex="0"
            (click)="deselectDrawing()"
            [style.visibility]="loading() ? 'hidden' : 'visible'"
          ></div>
          <svg
            class="drawing-layer"
            [class.drawing-active]="!!tool()"
            [style.visibility]="loading() || hiddenDrawings() ? 'hidden' : 'visible'"
            (click)="drawClick($event)"
            (pointerdown)="startBrush($event)"
            (pointerup)="endDrawingDrag($event); finishBrush($event)"
            (pointercancel)="cancelTool()"
            (pointermove)="previewDrawing($event)"
            aria-label="Chart drawings"
          >
            @for (shape of shapes(); track $index) {
              <g
                [class.drawing-object]="selected() && !tool() && !!shape.id"
                [class.drawing-object-selected]="selectedDrawingId() === shape.id"
                [attr.data-drawing-id]="shape.id"
                (pointerdown)="startDrawingDrag($event, shape.id)"
                (click)="selectShape($event, shape.id)"
              >
                @if (shape.kind === 'line') {
                  <path
                    [attr.d]="'M' + shape.x + ',' + shape.y + 'L' + shape.x2 + ',' + shape.y2"
                    stroke="transparent"
                    stroke-width="14"
                    fill="none"
                  />
                }
                @if (shape.kind === 'brush') {
                  <polyline
                    [attr.points]="shape.points"
                    [attr.stroke]="shape.color"
                    fill="none"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                  />
                } @else if (shape.kind === 'ellipse') {
                  <ellipse
                    [attr.cx]="shape.x + shape.w / 2"
                    [attr.cy]="shape.y + shape.h / 2"
                    [attr.rx]="shape.w / 2"
                    [attr.ry]="shape.h / 2"
                    [attr.stroke]="shape.color"
                    [attr.fill]="shape.color"
                    fill-opacity="0.1"
                  />
                } @else if (shape.kind === 'rect') {
                  <rect
                    [attr.x]="shape.x"
                    [attr.y]="shape.y"
                    [attr.width]="shape.w"
                    [attr.height]="shape.h"
                    [attr.stroke]="shape.color"
                    [attr.fill]="shape.color"
                    fill-opacity="0.1"
                  />
                } @else {
                  <line
                    [attr.x1]="shape.x"
                    [attr.y1]="shape.y"
                    [attr.x2]="shape.x2"
                    [attr.y2]="shape.y2"
                    [attr.stroke]="shape.color"
                    stroke-width="1.5"
                  />
                }
                @if (shape.label) {
                  <text
                    [attr.x]="shape.x + 5"
                    [attr.y]="shape.y - 5"
                    [attr.fill]="shape.color"
                    font-size="11"
                  >
                    {{ shape.label }}
                  </text>
                }
              </g>
            }
            @for (handle of drawingHandles(); track handle.key) {
              <circle
                class="drawing-handle"
                [attr.cx]="handle.x"
                [attr.cy]="handle.y"
                r="6"
                [attr.data-handle]="handle.key"
                (pointerdown)="startDrawingDrag($event, selectedDrawingId(), handle.key)"
                (click)="$event.stopPropagation()"
              />
            }
          </svg>
          @if (loading()) {
            <div class="chart-empty chart-loading" role="status">
              <span class="chart-spinner" aria-hidden="true"></span>
              <strong>Loading chart data…</strong
              ><span>Candles appear when loading is complete.</span>
            </div>
          } @else if (!data().length) {
            <div class="chart-empty">
              <span class="chart-empty-glyph">↗</span><strong>{{ emptyTitle() }}</strong
              ><span>{{ emptyText() }}</span>
            </div>
          } @else if (!selected()) {
            <button
              class="chart-activate"
              (click)="select(true)"
              aria-label="Activate chart interactions"
            >
              <span>Click to interact · scroll to move down the page</span>
            </button>
          } @else {
            <button
              class="chart-selection"
              (keydown.enter)="select(false)"
              (keydown.space)="$event.preventDefault(); select(false)"
            >
              Chart selected · scroll to zoom · double-click to unselect
            </button>
          }
        </div>
        <div
          class="oscillator"
          #oscillator
          [class.oscillator-visible]="hasOscillator()"
          [style.visibility]="loading() ? 'hidden' : 'visible'"
        ></div>
      </div>
    </div>`,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      position: relative;
      min-height: 280px;
      height: 100%;
      background: #101c2c;
      border-radius: 12px;
      overflow: hidden;
    }
    .chart-readout {
      flex-shrink: 0;
      height: 42px;
      box-sizing: border-box;
      padding: 8px 12px;
      color: #bdccdd;
      font: 11px/1.3 system-ui;
      overflow: hidden;
      display: flex;
      align-items: center;
    }
    .chart-host {
      height: 100%;
      min-height: 0;
    }
    .chart-body {
      display: flex;
      flex: 1;
      min-height: 0;
      position: relative;
    }
    .chart-main {
      display: flex;
      flex: 1;
      flex-direction: column;
      min-width: 0;
      min-height: 0;
    }
    .drawing-rail {
      width: 42px;
      flex: 0 0 42px;
      display: flex;
      align-items: center;
      flex-direction: column;
      gap: 3px;
      padding: 6px 3px;
      box-sizing: border-box;
      border-right: 1px solid #304455;
      background: #152335;
      overflow-y: auto;
      scrollbar-width: thin;
    }
    .drawing-rail button {
      flex: 0 0 30px;
      width: 32px;
      height: 30px;
      position: relative;
      padding: 5px;
      border: 1px solid transparent;
      border-radius: 5px;
      color: #bbcbdd;
      background: transparent;
    }
    .drawing-rail svg {
      display: block;
      width: 21px;
      height: 21px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.5;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .drawing-rail button:hover,
    .drawing-rail button.active {
      color: #5ed6b4;
      background: #254136;
      border-color: #4d907e;
    }
    .drawing-rail button:focus-visible {
      outline: 2px solid #5ed6b4;
    }
    .group-chevron {
      position: absolute;
      right: 0;
      bottom: 0;
      font-size: 10px;
    }
    .rail-divider {
      width: 24px;
      flex: 0 0 1px;
      background: #304455;
      margin: 4px 0;
    }
    .drawing-flyout {
      position: absolute;
      z-index: 15;
      top: 35px;
      left: 46px;
      width: 190px;
      max-width: calc(100% - 60px);
      padding: 8px;
      border: 1px solid #40556a;
      border-radius: 8px;
      background: #1c3045;
      box-shadow: 0 8px 24px #0008;
      color: #dce8f5;
      font: 12px/1.4 system-ui;
    }
    .drawing-flyout strong {
      display: block;
      margin: 4px 8px 8px;
      color: #8fa5bd;
      font-size: 10px;
      text-transform: uppercase;
    }
    .drawing-flyout button {
      display: block;
      width: 100%;
      text-align: left;
      background: none;
      border: 0;
      color: inherit;
      padding: 9px 8px;
      border-radius: 4px;
    }
    .drawing-flyout button:hover {
      background: #2a455b;
    }
    .study-legend {
      flex-shrink: 0;
      padding: 5px 10px;
      max-height: 100px;
      overflow: auto;
      color: #bdccdd;
      font: 11px/1.5 system-ui;
    }
    .study-legend > div {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 20px;
    }
    .study-legend strong {
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .study-legend button {
      margin-left: auto;
      background: transparent;
      color: #8fa5bd;
      border: 0;
      font-size: 16px;
    }
    .chart-plot {
      position: relative;
      flex: 1;
      min-height: 60px;
    }
    .oscillator {
      height: 0;
      overflow: hidden;
      flex-shrink: 0;
    }
    .oscillator-visible {
      height: 100px;
      border-top: 1px solid #304455;
    }
    .study-toolbar,
    .study-chips,
    .tool-hint,
    .drawing-list {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      padding: 6px 10px;
      color: #bdccdd;
      font: 11px/1.5 system-ui;
      flex-shrink: 0;
    }
    .study-toolbar {
      background: #192a3d;
    }
    .tool-hint {
      position: absolute;
      top: 42px;
      left: 48px;
      right: 8px;
      z-index: 13;
      min-height: 36px;
      box-sizing: border-box;
      border: 1px solid #5ed6b4;
      border-radius: 6px;
      background: #182d40f5;
      box-shadow: 0 4px 12px #0005;
    }
    button,
    select,
    input {
      font: inherit;
    }
    .study-toolbar button,
    .study-toolbar select,
    .study-toolbar input,
    .study-chips button,
    .drawing-list button,
    .tool-hint button,
    .drawing-editor button,
    .drawing-editor input,
    .tool-hint input {
      background: #203449;
      color: #e5edf7;
      border: 1px solid #40556a;
      border-radius: 5px;
      padding: 5px 7px;
      min-height: 28px;
    }
    .study-toolbar select {
      width: auto;
      flex: 0 1 auto;
      max-width: 100%;
      min-width: 0;
    }
    .study-toolbar input[type='number'] {
      width: 55px;
    }
    .study-toolbar input[type='color'] {
      width: 30px;
      height: 28px;
      flex: 0 0 30px;
      padding: 2px;
    }
    button {
      cursor: pointer;
    }
    button:disabled {
      opacity: 0.4;
      cursor: default;
    }
    .study-panel {
      position: absolute;
      top: 40px;
      left: 8px;
      right: 8px;
      z-index: 12;
      background: #152638;
      border: 1px solid #40556a;
      border-radius: 8px;
      box-shadow: 0 8px 24px #0008;
      max-height: 180px;
      overflow: auto;
    }
    .study-panel p {
      margin: 4px 12px 8px;
      color: #bdccdd;
      font: 11px/1.5 system-ui;
    }
    .drawing-list {
      max-height: 90px;
      overflow: auto;
    }
    .drawing-editor {
      position: absolute;
      top: 8px;
      right: 8px;
      z-index: 14;
      width: max-content;
      max-width: calc(100% - 64px);
      box-sizing: border-box;
      border: 1px solid #40556a;
      border-radius: 8px;
      box-shadow: 0 4px 12px #0005;
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
      padding: 8px 12px;
      background: #21354a;
      color: #e5edf7;
      font-size: 11px;
    }
    .drawing-editor label {
      margin: 0;
      min-width: 0;
      color: inherit;
      flex-direction: row;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .drawing-editor input {
      max-width: 150px;
    }
    .drawing-editor input[type='color'] {
      width: 30px;
      height: 26px;
      padding: 0;
    }
    .drawing-object {
      pointer-events: visiblePainted;
      cursor: move;
    }
    .drawing-object-selected {
      filter: drop-shadow(0 0 3px #fff8);
    }
    .drawing-handle {
      fill: #101c2c;
      stroke: #fff;
      stroke-width: 2px;
      pointer-events: all;
      cursor: grab;
      touch-action: none;
    }
    .drawing-object {
      touch-action: none;
    }
    .drawing-layer {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 4;
      pointer-events: none;
      overflow: hidden;
    }
    .drawing-active {
      pointer-events: auto;
      z-index: 8;
      cursor: crosshair;
      touch-action: none;
    }
    :host.chart-selected {
      box-shadow: inset 0 0 0 2px #5ed6b4;
    }
    :host.chart-selected::after {
      content: '';
      position: absolute;
      inset: 0;
      border: 2px solid #5ed6b4;
      border-radius: 12px;
      pointer-events: none;
      z-index: 7;
    }
    .chart-activate {
      position: absolute;
      inset: 0;
      z-index: 6;
      background: transparent;
      border: 0;
      cursor: pointer;
      color: #e5edf7;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 12px;
      touch-action: pan-y;
    }
    .chart-activate span,
    .chart-selection {
      background: #142638ed;
      border: 1px solid #5ed6b4;
      border-radius: 6px;
      padding: 7px 10px;
      color: #e5edf7;
      font: 11px/1.4 system-ui;
    }
    .chart-activate:focus-visible {
      outline: 2px solid #5ed6b4;
      outline-offset: -3px;
    }
    .chart-selection {
      position: absolute;
      top: 6px;
      left: 12px;
      right: 12px;
      width: fit-content;
      max-width: calc(100% - 24px);
      z-index: 6;
      cursor: pointer;
    }
    .chart-loading {
      background: #101c2c;
    }
    .chart-spinner {
      width: 28px;
      height: 28px;
      border: 3px solid #304455;
      border-top-color: #5ed6b4;
      border-radius: 50%;
      animation: chart-spin 0.8s linear infinite;
    }
    @keyframes chart-spin {
      to {
        transform: rotate(360deg);
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .chart-spinner {
        animation: none;
      }
    }
    .chart-empty {
      z-index: 5;
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      color: #e5edf7;
      pointer-events: none;
      padding: 28px;
      text-align: center;
    }
    .chart-empty span:last-child {
      font-size: 12px;
      color: #91a3ba;
      max-width: 290px;
      line-height: 1.6;
    }
    .chart-empty-glyph {
      font-size: 30px;
      color: #5ed6b4;
      border: 1px solid #304455;
      border-radius: 15px;
      padding: 8px 18px;
    }
  `,
})
export class Chart implements AfterViewInit, OnChanges, OnDestroy {
  context = input('');
  catalogue = STUDIES;
  panel = signal(false);
  objects = signal(false);
  studies = signal<Study[]>([]);
  studyKind: StudyKind = 'SMA';
  studyPeriod = 20;
  studyColor = '#f5c76b';
  studyMessage = signal('');
  studyValues = signal<Record<number, string>>({});
  studyDefinition() {
    return STUDIES.find((s) => s.kind === this.studyKind)!;
  }
  fixedPeriod() {
    return ['MACD', 'VWAP', 'Volume', 'OBV'].includes(this.studyKind);
  }
  hasOscillator() {
    return this.studies().some((s) => STUDIES.find((d) => d.kind === s.kind)?.pane);
  }
  private studySeries = new Map<number, ISeriesApi<any>[]>();
  private lowerChart?: IChartApi;
  private studyTimer?: ReturnType<typeof setTimeout>;
  private syncing = false;
  private lastContext = '';
  private api = inject(Api);
  layoutBusy = signal(false);
  layoutStatus = signal('');
  layoutReady = false;
  private layoutRequest = 0;
  async restoreLayout() {
    const context = this.context();
    const request = ++this.layoutRequest;
    this.layoutReady = false;
    this.layoutBusy.set(false);
    this.layoutStatus.set('');
    if (!context || context.startsWith(':') || this.type() !== 'candle') return;
    this.layoutBusy.set(true);
    this.layoutStatus.set('Loading saved settings…');
    const initial = JSON.stringify([this.studies(), this.drawings()]);
    try {
      const result = await this.api.get('/workspace/annotations' + query({ context }));
      if (request !== this.layoutRequest) return;
      // Do not replace an edit made while the settings request was in flight.
      if (result.layout && initial === JSON.stringify([this.studies(), this.drawings()])) {
        for (const study of [...this.studies()]) this.removeStudy(study.id);
        this.studies.set(result.layout.studies);
        const sourceInterval = drawingInterval(result.source_context || context);
        const point = (p: any) => ({ ...p, interval: p.interval || sourceInterval });
        this.drawings.set(
          result.layout.drawings.map((d: any) =>
            d.kind === 'brush'
              ? { ...d, points: d.points.map(point) }
              : { ...d, a: point(d.a), b: point(d.b) },
          ),
        );
        this.sequence = Math.max(
          0,
          ...this.studies().map((s) => s.id),
          ...this.drawings().map((d) => d.id),
        );
        this.updateStudies();
        this.renderDrawings();
      }
      this.layoutReady = true;
      this.layoutStatus.set('Use Save to keep changes.');
    } catch (e) {
      if (request === this.layoutRequest)
        this.layoutStatus.set('Could not load saved settings. ' + message(e));
    } finally {
      if (request === this.layoutRequest) this.layoutBusy.set(false);
    }
  }
  async saveLayout() {
    if (!this.layoutReady || this.layoutBusy()) return;
    const context = this.context();
    const request = this.layoutRequest;
    this.layoutBusy.set(true);
    this.layoutStatus.set('Saving…');
    try {
      await this.api.put('/workspace/annotations' + query({ context }), {
        studies: this.studies(),
        drawings: this.drawings(),
      });
      if (request === this.layoutRequest) this.layoutStatus.set('Saved to your account.');
    } catch (e) {
      if (request === this.layoutRequest) this.layoutStatus.set('Not saved. ' + message(e));
    } finally {
      if (request === this.layoutRequest) this.layoutBusy.set(false);
    }
  }
  @ViewChild('oscillator') oscillator!: ElementRef<HTMLElement>;
  addStudy() {
    const definition = this.studyDefinition();
    if (
      !this.fixedPeriod() &&
      (!Number.isInteger(this.studyPeriod) || this.studyPeriod < 2 || this.studyPeriod > 500)
    ) {
      this.studyMessage.set('Choose a whole-number period from 2 to 500.');
      return;
    }
    if (definition.volume && !this.data().some((r) => Number.isFinite(r.volume) && r.volume > 0)) {
      this.studyMessage.set(
        'This instrument has no traded-volume data. Choose a price-based indicator.',
      );
      return;
    }
    if (this.studyKind === 'VWAP' && typeof this.data()[0]?.time !== 'number') {
      this.studyMessage.set('Session VWAP needs intraday candles. Choose an intraday timeframe.');
      return;
    }
    if (this.studies().length >= 8 && (!definition.pane || !this.hasOscillator())) {
      this.studyMessage.set('Use up to eight indicators per chart. Remove one to add another.');
      return;
    }
    if (definition.pane)
      for (const s of this.studies())
        if (STUDIES.find((d) => d.kind === s.kind)?.pane) this.removeStudy(s.id);
    this.studies.update((items) => [
      ...items,
      {
        id: ++this.sequence,
        kind: this.studyKind,
        period: this.fixedPeriod() ? definition.period : this.studyPeriod,
        color: this.studyColor,
      },
    ]);
    this.studyMessage.set('');
    this.updateStudies();
  }
  removeStudy(id: number) {
    const study = this.studies().find((s) => s.id === id);
    const target = STUDIES.find((d) => d.kind === study?.kind)?.pane ? this.lowerChart : this.chart;
    for (const series of this.studySeries.get(id) || []) target?.removeSeries(series);
    this.studySeries.delete(id);
    this.studies.update((items) => items.filter((s) => s.id !== id));
  }
  private scheduleStudies() {
    if (!this.studies().length || this.studyTimer) return;
    this.studyTimer = setTimeout(() => {
      this.studyTimer = undefined;
      this.updateStudies();
    }, 250);
  }
  private updateStudies() {
    if (!this.chart || !this.lowerChart || this.loading()) return;
    const rows = this.data();
    for (const study of this.studies()) {
      const target = STUDIES.find((d) => d.kind === study.kind)?.pane
        ? this.lowerChart
        : this.chart;
      const result = calculateStudy(rows, study);
      this.studyValues.update((values) => ({
        ...values,
        [study.id]: result.values
          .map((v) =>
            Number.isFinite(v.at(-1))
              ? v.at(-1)!.toLocaleString('en-IN', { maximumFractionDigits: 2 })
              : '—',
          )
          .join(' / '),
      }));
      let series = this.studySeries.get(study.id);
      if (!series) {
        series = result.values.map((_, index) => {
          const options = {
            color: index === 1 ? '#86b9ff' : study.color,
            priceLineVisible: false,
            lastValueVisible: false,
            title: study.kind,
            ...(target === this.chart ? { autoscaleInfoProvider: () => null } : {}),
            ...(['RSI', 'Stochastic'].includes(study.kind)
              ? {
                  autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
                }
              : {}),
          };
          return result.histogram === index
            ? target.addHistogramSeries(options)
            : target.addLineSeries({ ...options, lineWidth: 1 });
        });
        for (const price of result.levels || [])
          series[0].createPriceLine({
            price,
            color: '#62748c',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: '',
          });
        this.studySeries.set(study.id, series);
      }
      result.values.forEach((values, index) =>
        series![index].setData(
          rows.map((row, i) =>
            Number.isFinite(values[i])
              ? {
                  time: row.time,
                  value: values[i],
                  ...(index === result.histogram
                    ? {
                        color: (study.kind === 'Volume' ? row.close >= row.open : values[i] >= 0)
                          ? '#4ecda6'
                          : '#f0798a',
                      }
                    : {}),
                }
              : { time: row.time },
          ),
        ),
      );
    }
    const range = this.chart.timeScale().getVisibleLogicalRange();
    if (range) this.lowerChart.timeScale().setVisibleLogicalRange(range);
  }
  tool = signal('');
  toolGroup = signal('');
  snap = signal(false);
  locked = signal(false);
  hiddenDrawings = signal(false);
  drawingGroups = [
    {
      label: 'Line tools',
      icon: 'M4 20L20 4M4 17v3h3M17 4h3v3',
      tools: [
        { kind: 'trend', label: 'Trend line' },
        { kind: 'ray', label: 'Ray' },
        { kind: 'horizontal', label: 'Horizontal level' },
        { kind: 'vertical', label: 'Vertical line' },
      ],
    },
    {
      label: 'Fibonacci retracement',
      icon: 'M3 4h18M3 9h14M3 14h18M3 20h14',
      tools: [{ kind: 'fib', label: 'Fibonacci retracement' }],
    },
    {
      label: 'Shapes',
      icon: 'M4 4h16v16H4zM2 2h4v4H2zM18 18h4v4h-4z',
      tools: [
        { kind: 'rectangle', label: 'Rectangle' },
        { kind: 'ellipse', label: 'Ellipse' },
      ],
    },
    {
      label: 'Text note',
      icon: 'M4 6V3h16v3M12 3v18m-4 0h8',
      tools: [{ kind: 'text', label: 'Text note' }],
    },
    {
      label: 'Brush',
      icon: 'M3 18c5-15 5 5 10-8s2 12 8-6M3 21h7',
      tools: [{ kind: 'brush', label: 'Brush' }],
    },
    {
      label: 'Measure price / bars',
      icon: 'M3 17L17 3l4 4L7 21zM7 13l3 3m0-6 3 3m0-6 3 3',
      tools: [{ kind: 'measure', label: 'Measure price / bars' }],
    },
  ];
  groupActive(group: any) {
    return (
      group.tools.some((item: any) => item.kind === this.tool()) || this.toolGroup() === group.label
    );
  }
  toggleToolGroup(label: string) {
    this.panel.set(false);
    this.toolGroup.set(this.toolGroup() === label ? '' : label);
  }
  toggleLock() {
    this.locked.set(!this.locked());
    this.cancelTool();
  }
  clearDrawings() {
    if (this.locked()) return;
    this.redo = [...this.drawings()].reverse();
    this.drawings.set([]);
    this.deselectDrawing();
    this.renderDrawings();
  }
  addRibbon() {
    if (this.studies().length > 4) {
      this.studyMessage.set('Remove indicators to make room for four EMA lines.');
      return;
    }
    const colors = ['#ffc107', '#ff9800', '#ff5722', '#ff4d6d'];
    this.studies.update((items) => [
      ...items,
      ...[10, 21, 50, 200].map((period, i): Study => ({
        id: ++this.sequence,
        kind: 'EMA',
        period,
        color: colors[i],
      })),
    ]);
    this.updateStudies();
    this.studyMessage.set('EMA ribbon added. The 200-period line needs 200 loaded candles.');
  }
  drawColor = '#f5c76b';
  note = '';
  anchor: any = null;
  drawings = signal<any[]>([]);
  shapes = signal<any[]>([]);
  redo: any[] = [];
  private sequence = 0;
  private preview: any = null;
  private stroke: any[] = [];
  startBrush(event: PointerEvent) {
    if (this.tool() !== 'brush' || this.locked()) return;
    const point = this.point(event);
    if (!point) return;
    event.preventDefault();
    (event.currentTarget as SVGElement).setPointerCapture(event.pointerId);
    this.stroke = [point];
  }
  finishBrush(event: PointerEvent) {
    if (this.tool() !== 'brush' || !this.stroke.length) return;
    if ((event.currentTarget as SVGElement).hasPointerCapture(event.pointerId))
      (event.currentTarget as SVGElement).releasePointerCapture(event.pointerId);
    if (this.stroke.length > 1) {
      this.drawings.update((items) => [
        ...items,
        { id: ++this.sequence, kind: 'brush', points: this.stroke, color: this.drawColor },
      ]);
      this.redo = [];
    }
    this.stroke = [];
    this.tool.set('');
    this.select(true);
    this.renderDrawings();
  }
  chooseTool(kind: string) {
    this.deselectDrawing();
    if (this.locked() && kind) return;
    this.toolGroup.set('');
    this.hiddenDrawings.set(false);
    this.panel.set(false);
    this.objects.set(false);
    this.anchor = null;
    this.preview = null;
    this.tool.set(kind);
    this.select(true);
    this.renderDrawings();
  }
  cancelTool() {
    this.deselectDrawing();
    this.stroke = [];
    this.toolGroup.set('');
    this.tool.set('');
    this.anchor = null;
    this.preview = null;
    this.renderDrawings();
    this.select(false);
  }
  undoDrawing() {
    if (this.locked()) return;
    const last = this.drawings().at(-1);
    if (last) {
      this.redo.push(last);
      this.drawings.update((d) => d.slice(0, -1));
      this.renderDrawings();
    }
  }
  redoDrawing() {
    if (this.locked()) return;
    const last = this.redo.pop();
    if (last) {
      this.drawings.update((d) => [...d, last]);
      this.renderDrawings();
    }
  }
  removeDrawing(id: number) {
    if (this.locked()) return;
    this.drawings.update((d) => d.filter((v) => v.id !== id));
    this.redo = [];
    this.renderDrawings();
  }
  selectedDrawingId = signal<number | null>(null);
  drawingHandles = signal<any[]>([]);
  private drawingDrag: any = null;
  selectedDrawing() {
    return this.drawings().find((d) => d.id === this.selectedDrawingId());
  }
  deselectDrawing() {
    this.selectedDrawingId.set(null);
    this.drawingHandles.set([]);
    this.drawingDrag = null;
  }
  selectDrawing(id: number) {
    this.tool.set('');
    this.anchor = null;
    this.preview = null;
    this.selectedDrawingId.set(id);
    this.select(true);
    this.renderDrawings();
  }
  selectShape(event: MouseEvent, id: number) {
    if (this.tool() || !id) return;
    event.stopPropagation();
    this.selectDrawing(id);
  }
  editSelected(patch: any) {
    if (this.locked()) return;
    this.drawings.update((items) =>
      items.map((d) => (d.id === this.selectedDrawingId() ? { ...d, ...patch } : d)),
    );
    this.redo = [];
    this.renderDrawings();
  }
  deleteSelected() {
    const drawing = this.selectedDrawing();
    if (!drawing || this.locked()) return;
    this.redo.push(drawing);
    this.drawings.update((items) => items.filter((d) => d.id !== drawing.id));
    this.deselectDrawing();
    this.renderDrawings();
  }
  drawingKey(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (
      !this.selected() ||
      !this.selectedDrawing() ||
      this.locked() ||
      target?.closest('input, textarea, select, [contenteditable="true"]') ||
      event.ctrlKey ||
      event.metaKey ||
      event.altKey
    )
      return;
    if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault();
      this.deleteSelected();
    }
  }
  startDrawingDrag(event: PointerEvent, id: number | null, handle = '') {
    if (this.tool() || !id || event.button !== 0) return;
    event.stopPropagation();
    this.selectDrawing(id);
    if (this.locked()) return;
    const point = this.point(event);
    if (!point) return;
    event.preventDefault();
    const svg = (event.currentTarget as SVGElement).ownerSVGElement!;
    svg.setPointerCapture(event.pointerId);
    this.drawingDrag = {
      id,
      handle,
      original: this.selectedDrawing(),
      x: event.clientX,
      price: point.price,
    };
  }
  endDrawingDrag(event: PointerEvent) {
    if (!this.drawingDrag) return;
    if ((event.currentTarget as SVGElement).hasPointerCapture(event.pointerId))
      (event.currentTarget as SVGElement).releasePointerCapture(event.pointerId);
    this.drawingDrag = null;
  }
  private pointX(point: any): number | null {
    const position = drawingPosition(point, this.drawingRows, drawingInterval(this.context()));
    if (position === null) return null;
    const index = Math.max(0, Math.min(this.drawingRows.length - 1, Math.floor(position)));
    const scale = this.chart!.timeScale();
    const x = scale.timeToCoordinate(this.drawingRows[index].time);
    return x === null ? null : x + (position - index) * scale.options().barSpacing;
  }

  private point(event: MouseEvent) {
    if (!this.chart || !this.series || this.loading() || !this.data().length) return null;
    const box = this.host.nativeElement.getBoundingClientRect();
    const x = event.clientX - box.left,
      y = event.clientY - box.top;
    if (x < 0 || x > this.chart.timeScale().width() || y < 0 || y > box.height - 26) return null;
    const scale = this.chart.timeScale();
    const logical = scale.coordinateToLogical(x);
    if (logical === null) return null;
    // Anchor empty-space points to the nearest loaded candle, preserving their bar offset.
    // coordinateToTime returns null to the right of the newest candle.
    const nearest = this.series.dataByIndex(
      Math.round(logical),
      logical < 0 ? MismatchDirection.NearestRight : MismatchDirection.NearestLeft,
    );
    const time = nearest?.time;
    if (time === undefined) return null;
    const baseX = scale.timeToCoordinate(time);
    if (baseX === null) return null;
    let price = this.series.coordinateToPrice(y);
    if (this.snap() && price !== null) {
      const logical = this.chart.timeScale().coordinateToLogical(x);
      const row: any = logical === null ? null : this.series.dataByIndex(Math.round(logical));
      if (row) {
        const prices = [row.open, row.high, row.low, row.close].filter(Number.isFinite);
        if (prices.length)
          price = prices.reduce((a, b) =>
            Math.abs(a - Number(price)) <= Math.abs(b - Number(price)) ? a : b,
          );
      }
    }
    return price === null
      ? null
      : {
          time,
          price,
          interval: drawingInterval(this.context()),
          offset: this.snap()
            ? Math.round((x - baseX) / scale.options().barSpacing)
            : (x - baseX) / scale.options().barSpacing,
        };
  }
  drawClick(event: MouseEvent) {
    if (!this.tool() || this.locked() || this.tool() === 'brush') return;
    event.stopPropagation();
    const p = this.point(event);
    if (!p) return;
    const single = ['horizontal', 'vertical', 'text'].includes(this.tool());
    if (!single && !this.anchor) {
      this.anchor = p;
      return;
    }
    this.drawings.update((d) => [
      ...d,
      {
        id: ++this.sequence,
        kind: this.tool(),
        a: this.anchor || p,
        b: p,
        color: this.drawColor,
        text: this.note.trim() || 'Note',
      },
    ]);
    this.redo = [];
    this.anchor = null;
    this.preview = null;
    this.tool.set('');
    this.select(true);
    this.renderDrawings();
  }
  previewDrawing(event: MouseEvent) {
    if (this.drawingDrag) {
      const p = this.point(event);
      if (!p) return;
      const drag = this.drawingDrag;
      if (drag.handle) this.editSelected({ [drag.handle]: p });
      else {
        const dx = (event.clientX - drag.x) / this.chart!.timeScale().options().barSpacing;
        const dy = p.price - drag.price;
        const move = (a: any) => {
          const interval = drawingInterval(this.context());
          const position = drawingPosition(a, this.drawingRows, interval)! + dx;
          const index = Math.max(0, Math.min(this.drawingRows.length - 1, Math.round(position)));
          return {
            time: this.drawingRows[index].time,
            interval,
            offset: position - index,
            price: a.price + dy,
          };
        };
        this.editSelected(
          drag.original.kind === 'brush'
            ? { points: drag.original.points.map(move) }
            : { a: move(drag.original.a), b: move(drag.original.b) },
        );
      }
      return;
    }
    if (this.tool() === 'brush' && this.stroke.length) {
      const point = this.point(event);
      if (point && this.stroke.length < 2000) {
        this.stroke.push(point);
        this.renderDrawings();
      }
      return;
    }
    if (this.anchor) {
      this.preview = this.point(event);
      this.renderDrawings();
    }
  }
  private renderDrawings() {
    if (!this.chart || !this.series) return;
    const shapes: any[] = [];
    const width = this.chart.timeScale().width(),
      height = this.host.nativeElement.clientHeight - 26;
    const all = [
      ...this.drawings(),
      ...(this.stroke.length
        ? [{ kind: 'brush', points: this.stroke, color: this.drawColor }]
        : []),
      ...(this.anchor && this.preview
        ? [{ kind: this.tool(), a: this.anchor, b: this.preview, color: this.drawColor }]
        : []),
    ];
    for (const d of all) {
      if (d.kind === 'brush') {
        const points = d.points
          .map((p: any) => {
            const x = this.pointX(p),
              y = this.series!.priceToCoordinate(p.price);
            return x === null || y === null ? null : `${x},${y}`;
          })
          .filter(Boolean)
          .join(' ');
        shapes.push({ id: d.id, kind: 'brush', points, color: d.color });
        continue;
      }
      const x = this.pointX(d.a),
        y = this.series.priceToCoordinate(d.a.price);
      const x2 = this.pointX(d.b),
        y2 = this.series.priceToCoordinate(d.b.price);
      if (x === null || x2 === null || y === null || y2 === null) continue;
      const line = (a: number, b: number, c: number, e: number, label = '') =>
        shapes.push({ id: d.id, kind: 'line', x: a, y: b, x2: c, y2: e, color: d.color, label });
      if (d.kind === 'horizontal') line(0, y, width, y, d.a.price.toFixed(2));
      else if (d.kind === 'vertical') line(x, 0, x, height);
      else if (d.kind === 'text') line(x, y, x, y, d.text);
      else if (d.kind === 'rectangle' || d.kind === 'ellipse')
        shapes.push({
          id: d.id,
          kind: d.kind === 'rectangle' ? 'rect' : 'ellipse',
          x: Math.min(x, x2),
          y: Math.min(y, y2),
          w: Math.abs(x2 - x),
          h: Math.abs(y2 - y),
          color: d.color,
        });
      else if (d.kind === 'fib')
        for (const ratio of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) {
          const py = y2 + (y - y2) * ratio;
          line(
            Math.min(x, x2),
            py,
            Math.max(x, x2),
            py,
            `${(ratio * 100).toFixed(1)}% · ${(d.b.price + (d.a.price - d.b.price) * ratio).toFixed(2)}`,
          );
        }
      else if (d.kind === 'ray' && x !== x2) {
        const end = x2 > x ? width : 0;
        line(x, y, end, y + ((y2 - y) * (end - x)) / (x2 - x));
      } else if (d.kind === 'measure') {
        const a = this.chart.timeScale().coordinateToLogical(x),
          b = this.chart.timeScale().coordinateToLogical(x2);
        line(
          x,
          y,
          x2,
          y2,
          `${(d.b.price - d.a.price).toFixed(2)} (${d.a.price ? ((d.b.price / d.a.price - 1) * 100).toFixed(2) : '—'}%) · ${Math.round(Math.abs(Number(b) - Number(a)))} bars`,
        );
      } else line(x, y, x2, y2);
    }
    const labels: { x: number; y: number }[] = [];
    for (const shape of shapes)
      if (shape.label) {
        if (labels.some((p) => Math.abs(p.y - shape.y) < 13 && Math.abs(p.x - shape.x) < 150))
          shape.label = '';
        else labels.push({ x: shape.x, y: shape.y });
      }
    this.shapes.set(shapes);
    const drawing = this.selectedDrawing();
    this.drawingHandles.set(
      drawing &&
        !this.tool() &&
        !this.locked() &&
        !this.hiddenDrawings() &&
        drawing.kind !== 'brush'
        ? (['horizontal', 'vertical', 'text'].includes(drawing.kind) ? ['a'] : ['a', 'b'])
            .map((key) => ({
              key,
              x: this.pointX(drawing[key]),
              y: this.series!.priceToCoordinate(drawing[key].price),
            }))
            .filter((p) => p.x !== null && p.y !== null)
        : [],
    );
  }
  loading = input(false);
  selected = signal(false);
  private element = inject(ElementRef<HTMLElement>);
  select(active: boolean) {
    this.selected.set(active && !this.loading() && !!this.data().length);
    this.chart?.applyOptions({
      handleScroll: this.selected() && !this.tool(),
      handleScale:
        this.selected() && !this.tool()
          ? {
              mouseWheel: true,
              pinch: true,
              axisPressedMouseMove: true,
              axisDoubleClickReset: false,
            }
          : false,
    });
    this.lowerChart?.applyOptions({
      handleScroll: this.selected() && !this.tool(),
      handleScale: this.selected() && !this.tool(),
    });
    if (this.selected()) this.host.nativeElement.focus({ preventScroll: true });
  }
  outside(event: Event) {
    if (!this.element.nativeElement.contains(event.target as Node)) this.cancelTool();
  }
  data = input<any[]>([]);
  type = input<'candle' | 'area'>('candle');
  fitUpdates = input(false);
  emptyTitle = input('Your market, in focus');
  emptyText = input('Connect your Kite account to see market data here.');
  @ViewChild('host') host!: ElementRef<HTMLElement>;
  readout = signal('');
  private fitted = false;
  fit() {
    this.chart?.timeScale().fitContent();
  }
  latest() {
    this.chart?.timeScale().scrollToRealTime();
  }
  private describe(row: any) {
    if (!row) return '';
    const number = (v: number) => v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    return (
      formatChartTime(row.time) +
      (this.type() === 'area'
        ? ' · ' + number(row.value)
        : ' · O ' +
          number(row.open) +
          ' H ' +
          number(row.high) +
          ' L ' +
          number(row.low) +
          ' C ' +
          number(row.close))
    );
  }
  private chart?: IChartApi;
  private series?: ISeriesApi<any>;
  private observer?: ResizeObserver;
  ngAfterViewInit() {
    this.chart = createChart(this.host.nativeElement, {
      handleScroll: false,
      handleScale: false,
      layout: {
        background: { type: ColorType.Solid, color: '#101c2c' },
        textColor: '#8295ac',
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: { vertLines: { color: '#1b2a3c' }, horzLines: { color: '#1b2a3c' } },
      rightPriceScale: { borderColor: '#26374a' },
      localization: { locale: 'en-GB', timeFormatter: formatChartTime },
      timeScale: {
        borderColor: '#26374a',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatChartTick,
      },
      crosshair: { vertLine: { color: '#63758a' }, horzLine: { color: '#63758a' } },
    });
    this.host.nativeElement.tabIndex = -1;
    this.lowerChart = createChart(this.oscillator.nativeElement, {
      width: this.host.nativeElement.clientWidth,
      height: 100,
      handleScroll: false,
      handleScale: false,
      layout: {
        background: { type: ColorType.Solid, color: '#101c2c' },
        textColor: '#8295ac',
        fontSize: 10,
      },
      grid: { vertLines: { color: '#1b2a3c' }, horzLines: { color: '#1b2a3c' } },
      rightPriceScale: { minimumWidth: 65 },
      timeScale: { timeVisible: true, tickMarkFormatter: formatChartTick },
      localization: { timeFormatter: formatChartTime },
    });
    this.chart.applyOptions({ rightPriceScale: { minimumWidth: 65 } });
    this.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && !this.syncing && this.hasOscillator()) {
        this.syncing = true;
        this.lowerChart?.timeScale().setVisibleLogicalRange(range);
        this.syncing = false;
      }
      this.renderDrawings();
    });
    this.lowerChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && !this.syncing && this.hasOscillator()) {
        this.syncing = true;
        this.chart?.timeScale().setVisibleLogicalRange(range);
        this.syncing = false;
      }
    });
    this.series =
      this.type() === 'area'
        ? this.chart.addAreaSeries({
            lineColor: '#55d9b0',
            topColor: '#27ad873d',
            bottomColor: '#27ad8700',
            lineWidth: 2,
          })
        : this.chart.addCandlestickSeries({
            upColor: '#4ecda6',
            downColor: '#f0798a',
            wickUpColor: '#4ecda6',
            wickDownColor: '#f0798a',
            borderVisible: false,
          });
    this.chart.subscribeCrosshairMove((param) => {
      const row = param.seriesData.get(this.series!) as any;
      this.readout.set(this.describe(row ? { ...row, time: param.time } : this.data().at(-1)));
      this.renderDrawings();
    });
    this.observer = new ResizeObserver((entries) => {
      const box = entries[0].contentRect;
      this.chart?.applyOptions({
        width: Math.floor(box.width),
        height: Math.max(40, Math.floor(box.height)),
      });
      this.lowerChart?.applyOptions({ width: Math.floor(box.width) });
      this.renderDrawings();
    });
    this.observer.observe(this.host.nativeElement);
    this.paint();
  }
  ngOnChanges() {
    if (drawingSymbol(this.context()) !== drawingSymbol(this.lastContext)) {
      this.drawings.set([]);
      for (const study of [...this.studies()]) this.removeStudy(study.id);
      this.redo = [];
      this.cancelTool();
      void this.restoreLayout();
    } else if (this.context() !== this.lastContext) {
      // Remove old-timeframe series before rebuilding them against the new candle dates.
      const settings = [...this.studies()];
      for (const study of settings) this.removeStudy(study.id);
      this.studies.set(settings);
      this.studyValues.set({});
    }
    if (this.loading() || this.context() !== this.lastContext) this.cancelTool();
    this.lastContext = this.context();
    this.paint();
  }
  private renderedData: any[] | null = null;
  private drawingRows: any[] = [];
  private paint() {
    if (!this.series) return;
    if (this.loading()) {
      if (this.renderedData !== null) this.series.setData([]);
      this.renderedData = null;
      this.readout.set('');
      this.fitted = false;
      return;
    }
    const data = this.data();
    if (data === this.renderedData) return;
    const previous = this.renderedData;
    if (previous?.length && data.length >= previous.length) {
      let prefix = 0;
      while (prefix < previous.length - 1 && data[prefix] === previous[prefix]) prefix++;
      const tail = data.slice(previous.length - 1);
      let last = chartDate(previous[previous.length - 1].time).getTime();
      const valid = tail.every((row, i) => {
        const stamp = chartDate(row.time).getTime();
        const ordered = Number.isFinite(stamp) && (i === 0 ? stamp === last : stamp > last);
        last = stamp;
        return ordered;
      });
      if (prefix === previous.length - 1 && valid && !this.fitUpdates()) {
        for (const row of tail) this.series.update(row);
        this.readout.set(this.describe(data.at(-1)));
        this.renderedData = data;
        this.drawingRows = data;
        this.scheduleStudies();
        this.renderDrawings();
        return;
      }
    }
    const unique = new Map<number, any>();
    for (const row of this.data()) {
      if (row.time == null) continue;
      const key = chartDate(row.time).getTime();
      if (Number.isFinite(key)) unique.set(key, row);
    }
    const rows = [...unique.entries()].sort(([a], [b]) => a - b).map(([, row]) => row);
    this.readout.set(this.describe(rows.at(-1)));
    this.chart?.applyOptions({ timeScale: { timeVisible: typeof rows[0]?.time === 'number' } });
    this.series.setData(rows);
    this.drawingRows = rows;
    this.renderedData = data;
    this.scheduleStudies();
    this.renderDrawings();
    if (rows.length && (!this.fitted || this.fitUpdates())) {
      this.chart?.timeScale().fitContent();
      this.fitted = true;
    }
    if (!rows.length) this.fitted = false;
  }
  ngOnDestroy() {
    this.layoutRequest++;
    if (this.studyTimer) clearTimeout(this.studyTimer);
    this.observer?.disconnect();
    this.lowerChart?.remove();
    this.chart?.remove();
  }
}
