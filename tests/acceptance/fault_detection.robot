*** Settings ***
Documentation     Acceptance tests: the analyser detects every injected fault.
...
...               Logs are real recordings from gpsd's regression suite (BSD-2-Clause,
...               see data/SOURCES.md). Faults are injected by gnsskit with a fixed seed.
...               NEO-M9N: 61 s static, DGPS fix throughout. ZED-F9P: 29 s static.
...               UM621: 20 s with a real 4 s loss of fix.
Library           GnssLibrary.py

*** Test Cases ***
Clean Static Log Has Full Availability And No Events
    Given the recorded log "ublox-zed-f9p-nmea.log"
    When the log is analysed
    Then fix availability is 100.0 %
    And 0 fix-lost events reported
    And no integrity problems reported

Static Log With A 30-Second Outage Reports One Fix Loss
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And a 30-second outage starting at 10 s
    When the log is analysed
    Then 1 fix-lost event reported
    And 1 fix-regained event reported
    And the outage lasts 30 seconds
    And fix availability is 50.8 %
    And no integrity problems reported

Real Receiver Loss Of Fix Is Detected Without Injection
    Given the recorded log "unicore-um621.log"
    When the log is analysed
    Then 1 fix-lost event reported
    And the outage lasts 4 seconds
    And fix availability is 80.0 %

Corrupted Checksums Are Counted And Do Not Cost The Fix
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And 5 corrupted checksums
    When the log is analysed
    Then 5 "bad_checksum" problems reported
    And fix availability is 100.0 %
    And 0 fix-lost events reported

Truncated Lines Are Counted
    Given the recorded log "ublox-zed-f9p-nmea.log"
    And 3 truncated lines
    When the log is analysed
    Then 3 "truncated" problems reported

Unknown Sentences And Garbage Are Counted
    Given the recorded log "ublox-zed-f9p-nmea.log"
    And 4 inserted unknown sentences
    And 2 inserted garbage lines
    When the log is analysed
    Then 4 "unknown_type" problems reported
    And 2 "non_nmea" problems reported

Missing Data Is A Data Gap Not A Fix Loss
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And 10 seconds of data removed starting at 20 s
    When the log is analysed
    Then 1 data gap of 10 seconds reported
    And 0 fix-lost events reported
    And fix availability is 83.6 %

Log Crossing Midnight Has One Rollover And Continuous Time
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And its times shifted to start 30 seconds before midnight
    When the log is analysed
    Then 1 UTC day rollover reported
    And all 61 epochs are 1 second apart
    And no integrity problems reported

All Faults Combined Are Each Detected
    Given the recorded log "ublox-neo-m9n-nmea.log"
    And 6 corrupted checksums
    And 4 truncated lines
    And 2 positions blanked
    And 3 inserted unknown sentences
    And 2 inserted garbage lines
    And 3 dropped GSV parts
    And a 15-second outage starting at 5 s
    And 5 seconds of data removed starting at 40 s
    When the log is analysed
    Then every fault in the manifest is detected
    And 1 fix-lost event reported
