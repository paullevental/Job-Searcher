import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import App from './App';

describe('App', () => {
  it('renders the application name', () => {
    render(<App />);

    expect(screen.getByRole('heading', { name: 'Job Searcher' })).toBeInTheDocument();
  });

  it('shows which workflow API it will talk to', () => {
    render(<App />);

    expect(screen.getByText('workflow API')).toBeInTheDocument();
  });
});
