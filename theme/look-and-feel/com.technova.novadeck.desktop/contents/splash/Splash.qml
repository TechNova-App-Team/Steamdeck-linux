/*
    NovaDeck splash screen: shown while switching to the desktop mode.
    Plain QtQuick only, so it works with Plasma 5 (Qt 5.15) and Plasma 6 (Qt 6).
*/
import QtQuick 2.15

Rectangle {
    id: root
    color: "#050506"

    // Set by ksplash, counts up while Plasma starts.
    property int stage

    onStageChanged: {
        if (stage >= 1 && !intro.running && content.opacity === 0) {
            intro.start()
        }
    }

    Image {
        id: glow
        source: "images/glow.png"
        width: Math.min(root.width, root.height) * 0.95
        height: width
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -root.height * 0.06
        opacity: content.opacity * 0.55
        smooth: true
    }

    Item {
        id: content
        anchors.centerIn: parent
        width: 360
        height: 260
        opacity: 0

        Image {
            id: logo
            source: "images/logo.svg"
            sourceSize.width: 112
            sourceSize.height: 112
            width: 112
            height: 112
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            smooth: true

            SequentialAnimation on scale {
                loops: Animation.Infinite
                running: content.opacity > 0
                NumberAnimation { from: 1.0; to: 1.06; duration: 1400; easing.type: Easing.InOutSine }
                NumberAnimation { from: 1.06; to: 1.0; duration: 1400; easing.type: Easing.InOutSine }
            }
        }

        Text {
            id: word
            text: "N O V A   D E C K"
            color: "#F5F7FA"
            font.pixelSize: 22
            font.weight: Font.Light
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: logo.bottom
            anchors.topMargin: 34
        }

        Rectangle {
            id: track
            width: 220
            height: 2
            radius: 1
            color: "#26FFFFFF"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: word.bottom
            anchors.topMargin: 30

            Rectangle {
                height: parent.height
                radius: 1
                color: "#F5F7FA"
                width: parent.width * Math.min(1, Math.max(0.08, root.stage / 6))
                Behavior on width {
                    NumberAnimation { duration: 700; easing.type: Easing.OutCubic }
                }
            }
        }
    }

    NumberAnimation {
        id: intro
        target: content
        property: "opacity"
        from: 0
        to: 1
        duration: 650
        easing.type: Easing.InOutQuad
    }
}
