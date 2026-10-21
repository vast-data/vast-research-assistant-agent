import {
  ChangeDetectionStrategy,
  Component,
  Input,
  signal
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIcon } from '@angular/material/icon';
import { AidrGuardContentBlock } from '../../../reports/services/session.mapper';

@Component({
  selector: 'app-aidr-guard-block',
  standalone: true,
  imports: [CommonModule, MatIcon],
  templateUrl: './aidr-guard-block.component.html',
  styleUrl: './aidr-guard-block.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush
})
export class AidrGuardBlockComponent {
  @Input({ required: true }) guard!: AidrGuardContentBlock;

  expanded = signal(false);

  toggle(): void {
    this.expanded.update(v => !v);
  }

  get displayName(): string {
    const typeLabels: Record<string, string> = {
      input: 'Input Guard',
      tool_output: 'Tool Output Guard',
      output: 'Output Guard',
    };
    return `AIDR ${typeLabels[this.guard.checkType] || 'Guard'}`;
  }

  get statusIcon(): string {
    if (this.guard.blocked) return 'gpp_bad';
    if (this.guard.anyDetected) return 'shield';
    return 'verified_user';
  }

  get statusClass(): string {
    if (this.guard.blocked) return 'blocked';
    if (this.guard.anyDetected) return 'detected';
    return 'clear';
  }

  get durationText(): string {
    return `${Math.round(this.guard.durationMs)}ms`;
  }

  get detectorEntries(): { name: string; detected: boolean; data?: any }[] {
    return Object.entries(this.guard.detectors).map(([name, info]) => ({
      name: this.formatDetectorName(name),
      detected: info.detected,
      data: info.data,
    }));
  }

  private formatDetectorName(name: string): string {
    return name
      .replace(/_/g, ' ')
      .replace(/\b\w/g, char => char.toUpperCase());
  }
}
