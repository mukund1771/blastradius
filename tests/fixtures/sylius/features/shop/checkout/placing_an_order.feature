@checkout
Feature: Placing an order (outside the configured paths: must never be read)
    Scenario: Completing the checkout
        When I complete my order
        Then I should see the thank you page
