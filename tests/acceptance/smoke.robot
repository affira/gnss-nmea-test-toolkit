*** Settings ***
Documentation     Smoke test: the gnsskit command is installed and runs.
Library           Process

*** Test Cases ***
CLI Reports Its Version
    Given the gnsskit command is installed
    When the user asks for the version
    Then the output is "gnsskit 0.1.0"

*** Keywords ***
The gnsskit command is installed
    ${result}=    Run Process    gnsskit    --help
    Should Be Equal As Integers    ${result.rc}    0

The user asks for the version
    ${result}=    Run Process    gnsskit    --version
    Set Test Variable    ${RESULT}    ${result}

The output is "${expected}"
    Should Be Equal As Integers    ${RESULT.rc}    0
    Should Be Equal    ${RESULT.stdout.strip()}    ${expected}
