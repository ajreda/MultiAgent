{
  "tasks": {
    "TASK-001": {
      "description": "Create HTML structure for responsive artwork gallery with fluid grid container supporting viewport-based column switching (3 columns desktop, 2 tablet, 1 mobile)",
      "state": "DONE",
      "dependencies": [],
      "acceptance_criteria": "Gallery renders correctly on desktop (3 columns), tablet (2 columns), and mobile (1 column) per viewport breakpoints"
    },
    "TASK-002": {
      "description": "Create HTML elements for artwork cards displaying title, price, description, and image thumbnail",
      "state": "TODO",
      "dependencies": [
        "TASK-001"
      ],
      "acceptance_criteria": "Each artwork card shows title, price, description, and image thumbnail; hover state is implemented"
    },
    "TASK-003": {
      "description": "Implement artwork card hover effect that displays title, price, and description tooltip within 200ms of mouse over",
      "state": "TODO",
      "dependencies": [
        "TASK-002"
      ],
      "acceptance_criteria": "Hover state shows artwork details tooltip within 200ms of mouse over"
    },
    "TASK-004": {
      "description": "Implement full-size image modal with expanded artwork details, gallery navigation (prev/next), and zoom functionality",
      "state": "TODO",
      "dependencies": [
        "TASK-003"
      ],
      "acceptance_criteria": "Modal opens on click within 500ms; displays full-resolution image with prev/next controls and zoom capability"
    },
    "TASK-005": {
      "description": "Implement navigation controls including breadcrumb trail, artwork category/type filter UI, and search bar with real-time filtering capability",
      "state": "TODO",
      "dependencies": [
        "TASK-002"
      ],
      "acceptance_criteria": "Breadcrumb displays current location; filter dropdown allows category selection; search input provides real-time filtered results"
    },
    "TASK-006": {
      "description": "Implement contact method section displaying artist's email address, phone number, and social media handles (Instagram, Twitter) with clickable links that open in new tab",
      "state": "TODO",
      "dependencies": [
        "TASK-002"
      ],
      "acceptance_criteria": "All contact links (email, phone, social) open in new tab when clicked; information displays correctly at bottom of page"
    }
  }
}