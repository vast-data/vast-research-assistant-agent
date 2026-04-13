import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIcon } from '@angular/material/icon';
import { MatTooltipModule } from '@angular/material/tooltip';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-dataflow-animation',
  standalone: true,
  imports: [CommonModule, MatIcon, MatTooltipModule, RouterLink],
  templateUrl: './dataflow-animation.component.html',
  styleUrl: './dataflow-animation.component.scss',
})
export class DataflowAnimationComponent {}
