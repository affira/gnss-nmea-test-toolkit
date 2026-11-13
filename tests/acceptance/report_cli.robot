*** Settings ***
Documentation     Black-box acceptance tests of the `gnsskit report` command.
...
...               Runs the installed command, then checks its exit code and the Markdown
...               report it writes. Exit codes: 0 PASS, 1 FAIL, 2 error, 3 INCOMPLETE.
...               Profiles in profiles/ hold illustrative example thresholds only.
Library           Process
Library           OperatingSystem

*** Variables ***
${DATA}           ${CURDIR}/../../data
${PROFILES}       ${CURDIR}/../../profiles
${WORK}           ${OUTPUT DIR}/report-cli

*** Test Cases ***
Cold Start Fails On Availability Under The Standard Profile
    Given the log "${DATA}/synthetic/SYNTHETIC-cold-start.log"
    When a report is generated
    Then the exit code is 1
    And the report verdict is FAIL
    And the report contains "| Fix availability | 75.0 % | ≥ 95.0 % | profile standard-gnss | **FAIL** |"
    And the report contains "| Time to first fix | 30.0 s | ≤ 60.0 s | profile standard-gnss | **PASS** |"

Static Log Passes When TTFF Is Disabled On The Command Line
    Given the log "${DATA}/synthetic/SYNTHETIC-static-10min.log"
    When a report is generated with    --max-ttff    none
    Then the exit code is 0
    And the report verdict is PASS
    And the report contains "| Time to first fix | not measurable | disabled | command-line override | NOT CHECKED |"

Log Starting With A Fix Is Incomplete By Default
    Given the log "${DATA}/synthetic/SYNTHETIC-static-10min.log"
    When a report is generated
    Then the exit code is 3
    And the report verdict is INCOMPLETE

High-Precision Profile Rejects Metre-Level CEP
    Given the log "${DATA}/synthetic/SYNTHETIC-static-10min.log"
    # The synthetic log's true position, so CEP is accuracy, not spread around the mean.
    When a report is generated with    --profile    ${PROFILES}/high-precision.yaml    --max-ttff    none
    ...    --ref    51.477928,-0.001545
    Then the exit code is 1
    And the report contains "given reference point 51.4779280, -0.0015450"
    And the report contains "| CEP95 | 2.406 m | ≤ 0.50 m | profile high-precision | **FAIL** |"
    And the report contains "Illustrative example values"

Fault-Injected Log Report Counts The Injected Checksum Errors
    Given the log "${DATA}/real/ublox-zed-f9p-nmea.log"
    And 3 checksums are corrupted by the injector
    When a report is generated
    Then the exit code is 1
    And the report contains "**FAULT-INJECTED LOG**"
    And the report contains "| bad_checksum | 3 | yes |"

Missing Log File Is An Error
    Given the log "${DATA}/real/does-not-exist.log"
    When a report is generated
    Then the exit code is 2

*** Keywords ***
The log "${path}"
    Create Directory    ${WORK}
    Set Test Variable    ${LOG}    ${path}
    Set Test Variable    ${REPORT}    ${WORK}/${TEST NAME}.md

${n} checksums are corrupted by the injector
    ${faulty}=    Set Variable    ${WORK}/${TEST NAME}.log
    ${result}=    Run Process    gnsskit    inject    ${LOG}    -o    ${faulty}
    ...    --seed    5    --bad-checksums    ${n}
    Should Be Equal As Integers    ${result.rc}    0    ${result.stderr}
    Set Test Variable    ${LOG}    ${faulty}

A report is generated
    A report is generated with

A report is generated with
    [Arguments]    @{options}
    ${result}=    Run Process    gnsskit    report    ${LOG}    -o    ${REPORT}    @{options}
    Set Test Variable    ${RESULT}    ${result}

The exit code is ${code}
    Should Be Equal As Integers    ${RESULT.rc}    ${code}    stderr: ${RESULT.stderr}

The report verdict is ${verdict}
    ${text}=    Get File    ${REPORT}
    Should Contain    ${text}    \n## Verdict: ${verdict}\n

The report contains "${expected}"
    ${text}=    Get File    ${REPORT}
    Should Contain    ${text}    ${expected}
